"""Data sources for the participatory-proposals query screen.

The screen reads a read-only analytical layer that does NOT live in the
Enlaight database. Two interchangeable backends are provided so the feature
can be developed and tested offline:

``sql``  queries the layer directly over a Django DB alias. Used locally
         (fixture loaded into the ``gold`` schema of the dev Postgres) and in
         any deployment granted a read-only credential to the warehouse.
``n8n``  proxies to an n8n webhook, following the same pattern as the
         ``/api/kb/*`` views: the shared secret travels in the ``key`` header.

Select with ``PROPOSALS_SOURCE``. Nothing in this module knows which
participatory programme it is serving.
"""

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from django.conf import settings
from django.db import connections

MAX_PAGE_SIZE = 200
EXPORT_MAX_ROWS = 50_000

# Only these may reach an ORDER BY clause.
PROPOSAL_ORDERINGS = {
    "recent": "publish_time DESC NULLS LAST, id DESC",
    "oldest": "publish_time ASC NULLS LAST, id ASC",
    "comments": "comments DESC NULLS LAST, id DESC",
    "votes": "votes DESC NULLS LAST, id DESC",
    "participants": "participants DESC NULLS LAST, id DESC",
    "title": "title ASC NULLS LAST",
}
PROCESS_ORDERINGS = {
    "proposals": "n_proposals DESC",
    "name": "name ASC",
    "recent": "start_date DESC NULLS LAST",
}

_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


class ProposalSourceError(RuntimeError):
    """Upstream data layer refused or could not be reached."""


def get_source() -> "BaseProposalSource":
    kind = getattr(settings, "PROPOSALS_SOURCE", "sql")
    if kind == "n8n":
        return N8nProposalSource()
    return SqlProposalSource()


class BaseProposalSource:
    def list_processes(self, **kw):  # pragma: no cover - interface
        raise NotImplementedError

    def list_components(self, **kw):  # pragma: no cover - interface
        raise NotImplementedError

    def list_proposals(self, **kw):  # pragma: no cover - interface
        raise NotImplementedError

    def facets(self, **kw):  # pragma: no cover - interface
        raise NotImplementedError


class SqlProposalSource(BaseProposalSource):
    """Reads the analytical layer over a Django database alias."""

    def __init__(self) -> None:
        self.alias = getattr(settings, "PROPOSALS_DB_ALIAS", "default")
        schema = getattr(settings, "PROPOSALS_SCHEMA", "gold")
        if not _SCHEMA_RE.match(schema):
            raise ProposalSourceError(f"invalid PROPOSALS_SCHEMA: {schema!r}")
        self.schema = schema

    # -- helpers ---------------------------------------------------------
    def _fetch(self, sql: str, params: Sequence[Any]) -> List[Dict[str, Any]]:
        with connections[self.alias].cursor() as cur:
            cur.execute(sql.format(s=self.schema), params)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def _scalar(self, sql: str, params: Sequence[Any]) -> Any:
        with connections[self.alias].cursor() as cur:
            cur.execute(sql.format(s=self.schema), params)
            row = cur.fetchone()
            return row[0] if row else None

    def _proposal_filters(
        self,
        process_id: int,
        component_ids: Optional[Sequence[int]],
        q: str,
        theme: Optional[str],
        status: Optional[str],
        date_from: Optional[str],
        date_to: Optional[str],
    ) -> Tuple[str, List[Any]]:
        where = ["p.process_id = %s"]
        params: List[Any] = [process_id]
        if component_ids:
            where.append("p.component_id = ANY(%s)")
            params.append(list(component_ids))
        if q:
            where.append(
                "to_tsvector('portuguese', coalesce(p.title,'') || ' ' || coalesce(p.content,'')) "
                "@@ plainto_tsquery('portuguese', %s)"
            )
            params.append(q)
        if theme:
            where.append("p.theme = %s" if theme != "__none__" else "p.theme IS NULL")
            if theme != "__none__":
                params.append(theme)
        if status:
            where.append("p.status = %s" if status != "__none__" else "p.status IS NULL")
            if status != "__none__":
                params.append(status)
        if date_from:
            where.append("p.publish_time >= %s")
            params.append(date_from)
        if date_to:
            where.append("p.publish_time < (%s::date + 1)")
            params.append(date_to)
        return " AND ".join(where), params

    # -- interface -------------------------------------------------------
    def list_processes(self, q="", ordering="proposals", page=1, page_size=24):
        where, params = "", []
        if q:
            where = "WHERE (pr.name ILIKE %s OR pr.description ILIKE %s)"
            params = [f"%{q}%", f"%{q}%"]
        order = PROCESS_ORDERINGS.get(ordering, PROCESS_ORDERINGS["proposals"])
        base = f"""
            FROM {{s}}.rag_process pr
            LEFT JOIN (
                SELECT process_id, count(*) AS n FROM {{s}}.rag_proposals GROUP BY 1
            ) c ON c.process_id = pr.process_id
            LEFT JOIN (
                SELECT process_id, count(*) AS n FROM {{s}}.rag_components GROUP BY 1
            ) k ON k.process_id = pr.process_id
            {where}
        """
        total = self._scalar("SELECT count(*) " + base, params)
        rows = self._fetch(
            f"""SELECT pr.process_id, pr.name, pr.type, pr.description,
                       pr.start_date, pr.end_date,
                       coalesce(c.n, 0) AS n_proposals,
                       coalesce(k.n, 0) AS n_components
                {base}
                ORDER BY {order}
                LIMIT %s OFFSET %s""",
            params + [page_size, (page - 1) * page_size],
        )
        return {"count": total, "results": rows}

    def list_components(self, process_id: int):
        return self._fetch(
            """SELECT k.id, k.menu_name, k.component_type,
                      coalesce(c.n, 0) AS n_proposals
               FROM {s}.rag_components k
               LEFT JOIN (
                   SELECT component_id, count(*) AS n
                   FROM {s}.rag_proposals WHERE process_id = %s GROUP BY 1
               ) c ON c.component_id = k.id
               WHERE k.process_id = %s
               ORDER BY coalesce(c.n, 0) DESC, k.id""",
            [process_id, process_id],
        )

    def list_proposals(
        self, process_id, component_ids=None, q="", theme=None, status=None,
        date_from=None, date_to=None, ordering="recent", page=1, page_size=25,
    ):
        where, params = self._proposal_filters(
            process_id, component_ids, q, theme, status, date_from, date_to
        )
        order = PROPOSAL_ORDERINGS.get(ordering, PROPOSAL_ORDERINGS["recent"])
        total = self._scalar(
            f"SELECT count(*) FROM {{s}}.rag_proposals p WHERE {where}", params
        )
        rows = self._fetch(
            f"""SELECT p.id, p.title, p.content, p.theme, p.status, p.uri,
                       p.publish_time, p.participants, p.comments, p.votes,
                       p.component_id, k.menu_name AS component_name
                FROM {{s}}.rag_proposals p
                LEFT JOIN {{s}}.rag_components k ON k.id = p.component_id
                WHERE {where}
                ORDER BY {order}
                LIMIT %s OFFSET %s""",
            params + [page_size, (page - 1) * page_size],
        )
        return {"count": total, "results": rows}

    def iter_proposals_for_export(self, **kw):
        """Yields rows for CSV, capped, without loading everything in memory."""
        kw.pop("page", None)
        kw.pop("page_size", None)
        where, params = self._proposal_filters(
            kw["process_id"], kw.get("component_ids"), kw.get("q", ""),
            kw.get("theme"), kw.get("status"), kw.get("date_from"), kw.get("date_to"),
        )
        order = PROPOSAL_ORDERINGS.get(kw.get("ordering"), PROPOSAL_ORDERINGS["recent"])
        sql = f"""SELECT p.id, p.title, p.content, p.theme, p.status, p.uri,
                         p.publish_time, p.participants, p.comments, p.votes,
                         p.component_id, k.menu_name AS component_name
                  FROM {{s}}.rag_proposals p
                  LEFT JOIN {{s}}.rag_components k ON k.id = p.component_id
                  WHERE {where}
                  ORDER BY {order}
                  LIMIT %s""".format(s=self.schema)
        with connections[self.alias].cursor() as cur:
            cur.execute(sql, params + [EXPORT_MAX_ROWS])
            cols = [c[0] for c in cur.description]
            yield cols
            while True:
                chunk = cur.fetchmany(1000)
                if not chunk:
                    break
                for row in chunk:
                    yield row

    def facets(self, process_id, component_ids=None):
        where, params = self._proposal_filters(
            process_id, component_ids, "", None, None, None, None
        )
        themes = self._fetch(
            f"""SELECT coalesce(p.theme, '__none__') AS value, count(*) AS n
                FROM {{s}}.rag_proposals p WHERE {where}
                GROUP BY 1 ORDER BY n DESC LIMIT 100""",
            params,
        )
        statuses = self._fetch(
            f"""SELECT coalesce(p.status, '__none__') AS value, count(*) AS n
                FROM {{s}}.rag_proposals p WHERE {where}
                GROUP BY 1 ORDER BY n DESC""",
            params,
        )
        span = self._fetch(
            f"""SELECT min(p.publish_time) AS first, max(p.publish_time) AS last
                FROM {{s}}.rag_proposals p WHERE {where}""",
            params,
        )
        return {"themes": themes, "statuses": statuses, "span": span[0] if span else {}}


class N8nProposalSource(BaseProposalSource):
    """Proxies to an n8n webhook, mirroring the /api/kb/* views."""

    def _call(self, path: str, payload: Dict[str, Any]) -> Any:
        base = getattr(settings, "N8N_BASE_URL", "")
        key = getattr(settings, "PROPOSALS_N8N_KEY", "") or getattr(settings, "N8N_KB_KEY", "")
        if not base or not key:
            raise ProposalSourceError(
                "Configuração do n8n ausente (N8N_BASE_URL / PROPOSALS_N8N_KEY)."
            )
        timeout = int(getattr(settings, "N8N_TIMEOUT", 30))
        try:
            r = requests.post(
                f"{base}{path}",
                json=payload,
                headers={"key": key, "Content-Type": "application/json"},
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise ProposalSourceError("Timeout ao consultar a fonte de dados.") from exc
        except requests.RequestException as exc:
            raise ProposalSourceError(f"Erro ao consultar a fonte de dados: {exc}") from exc
        if r.status_code >= 400:
            raise ProposalSourceError(f"Fonte de dados respondeu {r.status_code}.")
        try:
            return r.json()
        except ValueError as exc:
            raise ProposalSourceError("Fonte de dados devolveu resposta inválida.") from exc

    def list_processes(self, **kw):
        return self._call("/webhook/proposals/processes", kw)

    def list_components(self, process_id: int):
        return self._call("/webhook/proposals/components", {"process_id": process_id})

    def list_proposals(self, **kw):
        return self._call("/webhook/proposals/search", kw)

    def facets(self, **kw):
        return self._call("/webhook/proposals/facets", kw)

    def iter_proposals_for_export(self, **kw):
        data = self._call("/webhook/proposals/search", {**kw, "page": 1, "page_size": EXPORT_MAX_ROWS})
        rows = data.get("results", [])
        if not rows:
            yield []
            return
        cols = list(rows[0].keys())
        yield cols
        for r in rows:
            yield [r.get(c) for c in cols]
