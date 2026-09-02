import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertCircle,
  ArrowLeft,
  Download,
  FileSearch,
  FileText,
  Loader2,
  MessageSquare,
  Search,
  ThumbsUp,
  Users,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import {
  Facets,
  ParticipatoryProcess,
  Proposal,
  ProcessComponent,
  ProposalsService,
} from "@/services/ProposalsService";
import { ReportJob, ReportsService } from "@/services/ReportsService";

const PAGE_SIZE = 25;
const REPORT_POLL_MS = 5000;
const NONE = "__none__";
const ALL = "__all__";

/** Debounces a fast-changing value so typing does not fire a request per keystroke. */
function useDebounced<T>(value: T, delay = 350): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
}

const int = new Intl.NumberFormat("pt-BR");

function formatDate(value: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString("pt-BR");
}

function truncate(text: string | null, max = 180): string {
  if (!text) return "";
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > max ? `${clean.slice(0, max)}…` : clean;
}

function periodOf(p: ParticipatoryProcess): string {
  const a = p.start_date ? new Date(p.start_date).getFullYear() : null;
  const b = p.end_date ? new Date(p.end_date).getFullYear() : null;
  if (a && b) return a === b ? String(a) : `${a}–${b}`;
  return a ? String(a) : b ? String(b) : "—";
}

const ProposalsQuery = () => {
  const { t } = useTranslation();
  const { toast } = useToast();

  const [selected, setSelected] = useState<ParticipatoryProcess | null>(null);

  // ---- step 1: processes -------------------------------------------------
  const [processSearch, setProcessSearch] = useState("");
  const debouncedProcessSearch = useDebounced(processSearch);
  const [processes, setProcesses] = useState<ParticipatoryProcess[]>([]);
  const [processCount, setProcessCount] = useState(0);
  const [loadingProcesses, setLoadingProcesses] = useState(true);

  useEffect(() => {
    if (selected) return;
    let cancelled = false;
    setLoadingProcesses(true);
    ProposalsService.listProcesses({
      q: debouncedProcessSearch,
      ordering: "proposals",
      page_size: 40,
    })
      .then((data) => {
        if (cancelled) return;
        setProcesses(data.results);
        setProcessCount(data.count);
      })
      .catch(() => {
        if (!cancelled) {
          toast({
            title: t("proposals.loadError"),
            description: t("proposals.loadErrorDetail"),
            variant: "destructive",
          });
        }
      })
      .finally(() => !cancelled && setLoadingProcesses(false));
    return () => {
      cancelled = true;
    };
  }, [debouncedProcessSearch, selected, toast, t]);

  // ---- step 2: proposals -------------------------------------------------
  const [components, setComponents] = useState<ProcessComponent[]>([]);
  const [componentId, setComponentId] = useState<string>(ALL);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [theme, setTheme] = useState<string>(ALL);
  const [status, setStatus] = useState<string>(ALL);
  const [ordering, setOrdering] = useState("recent");
  const [text, setText] = useState("");
  const debouncedText = useDebounced(text);
  const [page, setPage] = useState(1);

  const [rows, setRows] = useState<Proposal[]>([]);
  const [total, setTotal] = useState(0);
  const [loadingRows, setLoadingRows] = useState(false);
  const [exporting, setExporting] = useState(false);

  // Geração de relatório: job assíncrono, ~3-4 min. A tela consulta o estado
  // em intervalos e só oferece o download quando o n8n devolve o arquivo.
  const [job, setJob] = useState<ReportJob | null>(null);
  const [starting, setStarting] = useState(false);
  const [elapsed, setElapsed] = useState(0);

  const query = useMemo(
    () => ({
      process_id: selected?.process_id ?? 0,
      component_ids: componentId === ALL ? undefined : [Number(componentId)],
      q: debouncedText,
      theme: theme === ALL ? undefined : theme,
      status: status === ALL ? undefined : status,
      ordering,
    }),
    [selected, componentId, debouncedText, theme, status, ordering],
  );

  // Filters change the result set, so any page but the first is meaningless.
  const firstRender = useRef(true);
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    setPage(1);
  }, [componentId, debouncedText, theme, status, ordering]);

  useEffect(() => {
    if (!selected) return;
    setComponentId(ALL);
    setTheme(ALL);
    setStatus(ALL);
    setText("");
    setPage(1);
    setJob(null);
    ProposalsService.listComponents(selected.process_id)
      .then(setComponents)
      .catch(() => setComponents([]));
    ProposalsService.facets({ process_id: selected.process_id })
      .then(setFacets)
      .catch(() => setFacets(null));
  }, [selected]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setLoadingRows(true);
    ProposalsService.search({ ...query, page, page_size: PAGE_SIZE })
      .then((data) => {
        if (cancelled) return;
        setRows(data.results);
        setTotal(data.count);
      })
      .catch(() => {
        if (!cancelled) {
          toast({
            title: t("proposals.loadError"),
            description: t("proposals.loadErrorDetail"),
            variant: "destructive",
          });
        }
      })
      .finally(() => !cancelled && setLoadingRows(false));
    return () => {
      cancelled = true;
    };
  }, [query, page, selected, toast, t]);

  const handleExport = useCallback(async () => {
    if (!selected) return;
    setExporting(true);
    try {
      await ProposalsService.exportCsv(query);
    } catch {
      toast({
        title: t("proposals.exportError"),
        description: t("proposals.exportErrorDetail"),
        variant: "destructive",
      });
    } finally {
      setExporting(false);
    }
  }, [query, selected, toast, t]);

  // Poll enquanto o job estiver aberto.
  useEffect(() => {
    if (!job || (job.status !== "pending" && job.status !== "running")) return;
    const id = setInterval(async () => {
      try {
        setJob(await ReportsService.get(job.id));
      } catch {
        /* erro transitório de rede: o próximo tick tenta de novo */
      }
    }, REPORT_POLL_MS);
    return () => clearInterval(id);
  }, [job]);

  // Cronômetro, para a espera de minutos não parecer travamento.
  useEffect(() => {
    if (!job || (job.status !== "pending" && job.status !== "running")) return;
    const started = new Date(job.created_at).getTime();
    const id = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(id);
  }, [job]);

  const handleGenerate = useCallback(async () => {
    if (!selected) return;
    setStarting(true);
    setElapsed(0);
    try {
      setJob(await ReportsService.create({
        process_id: selected.process_id,
        component_ids: componentId === ALL ? undefined : [Number(componentId)],
      }));
    } catch {
      toast({
        title: t("reports.startError"),
        description: t("reports.startErrorDetail"),
        variant: "destructive",
      });
    } finally {
      setStarting(false);
    }
  }, [selected, componentId, toast, t]);

  const handleDownloadReport = useCallback(async () => {
    if (!job) return;
    try {
      await ReportsService.download(job);
    } catch {
      toast({
        title: t("reports.downloadError"),
        description: t("reports.downloadErrorDetail"),
        variant: "destructive",
      });
    }
  }, [job, toast, t]);

  const jobRunning = job?.status === "pending" || job?.status === "running";
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const showingFrom = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const showingTo = Math.min(page * PAGE_SIZE, total);

  // ---- step 1 view -------------------------------------------------------
  if (!selected) {
    return (
      <main className="container mt-5 pb-16">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground flex items-center gap-2">
            <FileSearch className="h-6 w-6 text-primary" />
            {t("proposals.title")}
          </h1>
          <p className="text-muted-foreground text-sm">{t("proposals.pickProcess")}</p>
        </div>

        <div className="relative mt-6 mb-5 w-full sm:w-96">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="pl-9"
            placeholder={t("proposals.searchProcess")}
            value={processSearch}
            onChange={(e) => setProcessSearch(e.target.value)}
          />
        </div>

        {loadingProcesses ? (
          <div className="space-y-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-20 w-full" />
            ))}
          </div>
        ) : processes.length === 0 ? (
          <Card className="p-10 text-center text-sm text-muted-foreground">
            {t("proposals.noProcess")}
          </Card>
        ) : (
          <>
            <p className="text-xs text-muted-foreground mb-3">
              {t("proposals.processCount", { count: processCount })}
            </p>
            <div className="space-y-2">
              {processes.map((p) => (
                <Card
                  key={p.process_id}
                  role="button"
                  tabIndex={0}
                  onClick={() => setSelected(p)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setSelected(p);
                    }
                  }}
                  className="flex flex-col gap-2 p-4 cursor-pointer transition-colors hover:border-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-primary sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="min-w-0">
                    <div className="font-medium truncate">{p.name || `#${p.process_id}`}</div>
                    <div className="text-xs text-muted-foreground mt-0.5">
                      {[p.type, periodOf(p)].filter(Boolean).join(" · ")}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-5 text-sm">
                    <div className="text-right">
                      <div className="font-semibold">{int.format(p.n_proposals)}</div>
                      <div className="text-[11px] text-muted-foreground">
                        {t("proposals.proposals")}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="font-semibold">{int.format(p.n_components)}</div>
                      <div className="text-[11px] text-muted-foreground">
                        {t("proposals.components")}
                      </div>
                    </div>
                  </div>
                </Card>
              ))}
            </div>
          </>
        )}
      </main>
    );
  }

  // ---- step 2 view -------------------------------------------------------
  return (
    <main className="container mt-5 pb-16">
      <Button
        variant="ghost"
        size="sm"
        className="mb-3 -ml-2 text-muted-foreground"
        onClick={() => setSelected(null)}
      >
        <ArrowLeft className="mr-1 h-4 w-4" />
        {t("proposals.backToProcesses")}
      </Button>

      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold tracking-tight text-foreground truncate">
            {selected.name || `#${selected.process_id}`}
          </h1>
          <p className="text-muted-foreground text-sm">
            {loadingRows
              ? t("proposals.counting")
              : t("proposals.matchCount", { count: total })}
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button
            variant="outline"
            onClick={handleExport}
            disabled={exporting || total === 0}
          >
            {exporting ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Download className="mr-2 h-4 w-4" />
            )}
            {t("proposals.downloadCsv")}
          </Button>
          <Button
            onClick={handleGenerate}
            disabled={starting || total === 0 || jobRunning}
          >
            {starting || jobRunning ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <FileText className="mr-2 h-4 w-4" />
            )}
            {t("reports.generate")}
          </Button>
        </div>
      </div>

      {job && (
        <div className="mt-4 rounded-md border bg-card p-4">
          {jobRunning && (
            <div className="flex items-start gap-3">
              <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-primary" />
              <div className="min-w-0">
                <div className="text-sm font-medium">{t("reports.running")}</div>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {t("reports.runningHint", {
                    elapsed: `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`,
                  })}
                </p>
              </div>
            </div>
          )}

          {job.status === "succeeded" && (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-start gap-3 min-w-0">
                <FileText className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{job.filename}</div>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {t("reports.ready", {
                      size: ((job.size_bytes ?? 0) / 1048576).toFixed(1),
                    })}
                  </p>
                </div>
              </div>
              <Button size="sm" onClick={handleDownloadReport} className="shrink-0">
                <Download className="mr-2 h-4 w-4" />
                {t("reports.download")}
              </Button>
            </div>
          )}

          {job.status === "failed" && (
            <div className="flex items-start gap-3">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
              <div className="min-w-0">
                <div className="text-sm font-medium">{t("reports.failed")}</div>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {job.error || t("reports.failedGeneric")}
                </p>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="mt-6 mb-4 flex flex-col gap-3 lg:flex-row lg:flex-wrap lg:items-end">
        <div className="relative w-full lg:w-72">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="pl-9"
            placeholder={t("proposals.searchProposals")}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </div>

        <Select value={componentId} onValueChange={setComponentId}>
          <SelectTrigger className="w-full lg:w-72">
            <SelectValue placeholder={t("proposals.allComponents")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("proposals.allComponents")}</SelectItem>
            {components
              .filter((c) => c.n_proposals > 0)
              .map((c) => (
                <SelectItem key={c.id} value={String(c.id)}>
                  {truncate(c.menu_name || `#${c.id}`, 48)} ({int.format(c.n_proposals)})
                </SelectItem>
              ))}
          </SelectContent>
        </Select>

        {/* Eixo e status só aparecem quando o processo escolhido de fato os usa. */}
        {facets && facets.themes.some((f) => f.value !== NONE) && (
          <Select value={theme} onValueChange={setTheme}>
            <SelectTrigger className="w-full lg:w-56">
              <SelectValue placeholder={t("proposals.allThemes")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("proposals.allThemes")}</SelectItem>
              {facets.themes.map((f) => (
                <SelectItem key={f.value} value={f.value}>
                  {f.value === NONE ? t("proposals.noTheme") : truncate(f.value, 40)} (
                  {int.format(f.n)})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        {facets && facets.statuses.some((f) => f.value !== NONE) && (
          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger className="w-full lg:w-48">
              <SelectValue placeholder={t("proposals.allStatuses")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("proposals.allStatuses")}</SelectItem>
              {facets.statuses.map((f) => (
                <SelectItem key={f.value} value={f.value}>
                  {f.value === NONE ? t("proposals.noStatus") : f.value} ({int.format(f.n)})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        <div className="lg:ml-auto">
          <Select value={ordering} onValueChange={setOrdering}>
            <SelectTrigger className="w-full lg:w-52">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="recent">{t("proposals.sortRecent")}</SelectItem>
              <SelectItem value="oldest">{t("proposals.sortOldest")}</SelectItem>
              <SelectItem value="comments">{t("proposals.sortComments")}</SelectItem>
              <SelectItem value="votes">{t("proposals.sortVotes")}</SelectItem>
              <SelectItem value="participants">{t("proposals.sortParticipants")}</SelectItem>
              <SelectItem value="title">{t("proposals.sortTitle")}</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="min-w-[260px]">{t("proposals.colTitle")}</TableHead>
                <TableHead className="min-w-[300px]">{t("proposals.colSummary")}</TableHead>
                <TableHead className="min-w-[150px]">{t("proposals.colComponent")}</TableHead>
                <TableHead className="w-[100px]">{t("proposals.colDate")}</TableHead>
                <TableHead className="w-[80px] text-right">
                  <MessageSquare className="ml-auto h-4 w-4" aria-label={t("proposals.colComments")} />
                </TableHead>
                <TableHead className="w-[80px] text-right">
                  <ThumbsUp className="ml-auto h-4 w-4" aria-label={t("proposals.colVotes")} />
                </TableHead>
                <TableHead className="w-[80px] text-right">
                  <Users className="ml-auto h-4 w-4" aria-label={t("proposals.colParticipants")} />
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loadingRows ? (
                Array.from({ length: 8 }).map((_, i) => (
                  <TableRow key={i}>
                    <TableCell colSpan={7}>
                      <Skeleton className="h-6 w-full" />
                    </TableCell>
                  </TableRow>
                ))
              ) : rows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="py-12 text-center text-sm text-muted-foreground">
                    {t("proposals.noResults")}
                  </TableCell>
                </TableRow>
              ) : (
                rows.map((r) => (
                  <TableRow key={r.id} className="align-top">
                    <TableCell>
                      <div className="font-medium leading-snug">{r.title || "—"}</div>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5">
                        <span className="text-[11px] text-muted-foreground">#{r.id}</span>
                        {r.theme && (
                          <Badge variant="secondary" className="font-normal">
                            {truncate(r.theme, 28)}
                          </Badge>
                        )}
                        {r.status && (
                          <Badge variant="outline" className="font-normal">
                            {r.status}
                          </Badge>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground leading-relaxed">
                      {truncate(r.content)}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {truncate(r.component_name, 40) || "—"}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground whitespace-nowrap">
                      {formatDate(r.publish_time)}
                    </TableCell>
                    <TableCell className="text-right text-sm tabular-nums">
                      {int.format(r.comments ?? 0)}
                    </TableCell>
                    <TableCell className="text-right text-sm tabular-nums">
                      {int.format(r.votes ?? 0)}
                    </TableCell>
                    <TableCell className="text-right text-sm tabular-nums">
                      {int.format(r.participants ?? 0)}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>

        <div className="flex flex-col gap-3 border-t bg-muted/30 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <span className="text-xs text-muted-foreground">
            {t("proposals.showing", { from: showingFrom, to: showingTo, total })}
          </span>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1 || loadingRows}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              {t("proposals.previous")}
            </Button>
            <span className="text-xs text-muted-foreground tabular-nums">
              {t("proposals.pageOf", { page, totalPages })}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages || loadingRows}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            >
              {t("proposals.next")}
            </Button>
          </div>
        </div>
      </Card>
    </main>
  );
};

export default ProposalsQuery;
