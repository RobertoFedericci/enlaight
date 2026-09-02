import api from "@/services/api";

export type ReportStatus = "pending" | "running" | "succeeded" | "failed";

export interface ReportJob {
  id: string;
  status: ReportStatus;
  error: string;
  params: { process_id: number; component_ids?: number[] };
  filename: string;
  size_bytes: number | null;
  process_name: string;
  created_at: string;
  finished_at: string | null;
  download_url: string | null;
}

export const ReportsService = {
  /** Starts generation. Responds 202 at once — the workflow runs for minutes. */
  async create(params: { process_id: number; component_ids?: number[] }) {
    const { data } = await api.post("/reports/jobs/", params);
    return data as ReportJob;
  },

  async get(id: string) {
    const { data } = await api.get(`/reports/jobs/${id}/`);
    return data as ReportJob;
  },

  async list() {
    const { data } = await api.get("/reports/jobs/");
    return (data as { results: ReportJob[] }).results;
  },

  /**
   * Downloads the artefact. Fetched as a blob because the route is
   * JWT-authenticated — a plain <a href> would not carry the token.
   */
  async download(job: ReportJob) {
    const response = await api.get(`/reports/jobs/${job.id}/download/`, {
      responseType: "blob",
    });
    const url = URL.createObjectURL(new Blob([response.data]));
    const a = document.createElement("a");
    a.href = url;
    a.download = job.filename || `relatorio_${job.id}.docx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
};
