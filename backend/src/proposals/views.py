"""Read-only query API over the participatory-proposals analytical layer.

Every view is behind ``PROPOSALS_ENABLED`` (default off) so merging this app
cannot change the behaviour of an existing deployment.
"""

import csv
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.http import Http404, StreamingHttpResponse
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .sources import (
    MAX_PAGE_SIZE,
    PROCESS_ORDERINGS,
    PROPOSAL_ORDERINGS,
    ProposalSourceError,
    get_source,
)


def _enabled() -> bool:
    return bool(getattr(settings, "PROPOSALS_ENABLED", False))


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def _id_list(raw: Optional[str]) -> Optional[List[int]]:
    if not raw:
        return None
    out = []
    for part in str(raw).split(","):
        part = part.strip()
        if part:
            try:
                out.append(int(part))
            except ValueError:
                continue
    return out or None


class _Base(APIView):
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        if not _enabled():
            raise Http404("Consulta de propostas não está habilitada nesta instalação.")
        return super().initial(request, *args, **kwargs)

    def handle_source_error(self, exc: ProposalSourceError) -> Response:
        return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)


_Q = openapi.Parameter("q", openapi.IN_QUERY, type=openapi.TYPE_STRING, description="Busca textual")
_PROCESS = openapi.Parameter(
    "process_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True,
    description="Processo participativo",
)
_COMPONENTS = openapi.Parameter(
    "component_ids", openapi.IN_QUERY, type=openapi.TYPE_STRING,
    description="IDs de componente separados por vírgula (vazio = todos)",
)


class ProcessListView(_Base):
    @swagger_auto_schema(
        tags=["proposals"],
        operation_id="proposals_processes",
        summary="Lista processos participativos com contagem de propostas",
        manual_parameters=[_Q],
    )
    def get(self, request):
        p = request.query_params
        try:
            data = get_source().list_processes(
                q=(p.get("q") or "").strip(),
                ordering=p.get("ordering") if p.get("ordering") in PROCESS_ORDERINGS else "proposals",
                page=_int(p.get("page"), 1, 1, 10_000),
                page_size=_int(p.get("page_size"), 24, 1, MAX_PAGE_SIZE),
            )
        except ProposalSourceError as exc:
            return self.handle_source_error(exc)
        return Response(data)


class ComponentListView(_Base):
    @swagger_auto_schema(
        tags=["proposals"],
        operation_id="proposals_components",
        summary="Lista componentes de um processo, com contagem de propostas",
    )
    def get(self, request, process_id: int):
        try:
            return Response({"results": get_source().list_components(process_id)})
        except ProposalSourceError as exc:
            return self.handle_source_error(exc)


class ProposalListView(_Base):
    @swagger_auto_schema(
        tags=["proposals"],
        operation_id="proposals_search",
        summary="Consulta paginada de propostas",
        manual_parameters=[_PROCESS, _COMPONENTS, _Q],
    )
    def get(self, request):
        p = request.query_params
        process_id = _int(p.get("process_id"), 0, 0, 2**31 - 1)
        if not process_id:
            return Response(
                {"detail": "Parâmetro 'process_id' é obrigatório."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            data = get_source().list_proposals(
                process_id=process_id,
                component_ids=_id_list(p.get("component_ids")),
                q=(p.get("q") or "").strip(),
                theme=p.get("theme") or None,
                status=p.get("status") or None,
                date_from=p.get("date_from") or None,
                date_to=p.get("date_to") or None,
                ordering=p.get("ordering") if p.get("ordering") in PROPOSAL_ORDERINGS else "recent",
                page=_int(p.get("page"), 1, 1, 100_000),
                page_size=_int(p.get("page_size"), 25, 1, MAX_PAGE_SIZE),
            )
        except ProposalSourceError as exc:
            return self.handle_source_error(exc)
        return Response(data)


class FacetsView(_Base):
    @swagger_auto_schema(
        tags=["proposals"],
        operation_id="proposals_facets",
        summary="Valores disponíveis de eixo e status para o escopo escolhido",
        manual_parameters=[_PROCESS, _COMPONENTS],
    )
    def get(self, request):
        p = request.query_params
        process_id = _int(p.get("process_id"), 0, 0, 2**31 - 1)
        if not process_id:
            return Response(
                {"detail": "Parâmetro 'process_id' é obrigatório."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            data = get_source().facets(
                process_id=process_id, component_ids=_id_list(p.get("component_ids"))
            )
        except ProposalSourceError as exc:
            return self.handle_source_error(exc)
        return Response(data)


class _Echo:
    """File-like object that returns what it is asked to write, for csv.writer."""

    def write(self, value):
        return value


class ExportView(_Base):
    @swagger_auto_schema(
        tags=["proposals"],
        operation_id="proposals_export",
        summary="Exporta a consulta corrente em CSV",
        manual_parameters=[_PROCESS, _COMPONENTS, _Q],
        produces=["text/csv"],
    )
    def get(self, request):
        p = request.query_params
        process_id = _int(p.get("process_id"), 0, 0, 2**31 - 1)
        if not process_id:
            return Response(
                {"detail": "Parâmetro 'process_id' é obrigatório."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        kwargs: Dict[str, Any] = {
            "process_id": process_id,
            "component_ids": _id_list(p.get("component_ids")),
            "q": (p.get("q") or "").strip(),
            "theme": p.get("theme") or None,
            "status": p.get("status") or None,
            "date_from": p.get("date_from") or None,
            "date_to": p.get("date_to") or None,
            "ordering": p.get("ordering") if p.get("ordering") in PROPOSAL_ORDERINGS else "recent",
        }
        writer = csv.writer(_Echo())

        def stream():
            # BOM so Excel opens the accented columns correctly.
            yield "﻿"
            for row in get_source().iter_proposals_for_export(**kwargs):
                yield writer.writerow(row)

        response = StreamingHttpResponse(stream(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="propostas_processo_{process_id}.csv"'
        )
        return response
