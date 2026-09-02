import api, { API_BASE } from "@/services/api";

export interface ParticipatoryProcess {
  process_id: number;
  name: string | null;
  type: string | null;
  description: string | null;
  start_date: string | null;
  end_date: string | null;
  n_proposals: number;
  n_components: number;
}

export interface ProcessComponent {
  id: number;
  menu_name: string | null;
  component_type: string | null;
  n_proposals: number;
}

export interface Proposal {
  id: number;
  title: string | null;
  content: string | null;
  theme: string | null;
  status: string | null;
  uri: string | null;
  publish_time: string | null;
  participants: number | null;
  comments: number | null;
  votes: number | null;
  component_id: number | null;
  component_name: string | null;
}

export interface Facet {
  value: string;
  n: number;
}

export interface Facets {
  themes: Facet[];
  statuses: Facet[];
  span: { first: string | null; last: string | null };
}

export interface ProposalQuery {
  process_id: number;
  component_ids?: number[];
  q?: string;
  theme?: string;
  status?: string;
  date_from?: string;
  date_to?: string;
  ordering?: string;
  page?: number;
  page_size?: number;
}

/** Drops empty values and flattens component_ids into the CSV the API expects. */
function toParams(query: ProposalQuery): Record<string, string | number> {
  const out: Record<string, string | number> = { process_id: query.process_id };
  if (query.component_ids?.length) out.component_ids = query.component_ids.join(",");
  if (query.q) out.q = query.q;
  if (query.theme) out.theme = query.theme;
  if (query.status) out.status = query.status;
  if (query.date_from) out.date_from = query.date_from;
  if (query.date_to) out.date_to = query.date_to;
  if (query.ordering) out.ordering = query.ordering;
  if (query.page) out.page = query.page;
  if (query.page_size) out.page_size = query.page_size;
  return out;
}

export const ProposalsService = {
  async listProcesses(params: { q?: string; ordering?: string; page?: number; page_size?: number }) {
    const { data } = await api.get("/proposals/processes/", { params });
    return data as { count: number; results: ParticipatoryProcess[] };
  },

  async listComponents(processId: number) {
    const { data } = await api.get(`/proposals/processes/${processId}/components/`);
    return (data as { results: ProcessComponent[] }).results;
  },

  async search(query: ProposalQuery) {
    const { data } = await api.get("/proposals/", { params: toParams(query) });
    return data as { count: number; results: Proposal[] };
  },

  async facets(query: Pick<ProposalQuery, "process_id" | "component_ids">) {
    const { data } = await api.get("/proposals/facets/", {
      params: toParams(query as ProposalQuery),
    });
    return data as Facets;
  },

  /**
   * Downloads the current query as CSV.
   *
   * Fetched as a blob rather than linked directly: the export route is
   * authenticated, and a plain <a href> would not carry the bearer token.
   */
  async exportCsv(query: ProposalQuery) {
    const response = await api.get("/proposals/export/", {
      params: toParams(query),
      responseType: "blob",
    });
    const url = URL.createObjectURL(new Blob([response.data], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `propostas_processo_${query.process_id}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },

  exportUrl(query: ProposalQuery) {
    const qs = new URLSearchParams(
      Object.entries(toParams(query)).map(([k, v]) => [k, String(v)]),
    );
    return `${API_BASE}/proposals/export/?${qs.toString()}`;
  },
};
