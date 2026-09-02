"""Start, track and download generated reports.

Flow: POST /jobs/ creates a row and fires the n8n webhook, returning 202 at
once. The workflow runs for a couple of minutes and POSTs the finished .docx
to /jobs/<id>/callback/, authenticated by a shared secret in the `key` header
(its own, deliberately not the KB one). The UI polls /jobs/<id>/ and then
downloads through /jobs/<id>/download/, which is JWT-authenticated.
"""

import logging
import threading
from datetime import timedelta

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404
from django.utils import timezone
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ReportJob
from .serializers import ReportJobCreateSerializer, ReportJobSerializer

logger = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=15)


def _enabled():
    return bool(getattr(settings, "REPORTS_ENABLED", False))


def _expire_if_stale(job):
    """A workflow that dies mid-run never calls back; without this the job
    would sit on 'running' forever and the UI would poll for ever."""
    if job.is_open and timezone.now() - job.created_at > STALE_AFTER:
        job.status = ReportJob.FAILED
        job.error = "A geração passou de 15 minutos sem retorno. Verifique a execução no n8n."
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at"])
    return job


def _fire_webhook(job_id, payload, url, timeout):
    """Runs off the request thread. The webhook answers immediately
    (responseMode: onReceived), so this only needs to survive the handshake."""
    try:
        r = requests.post(url, json=payload, timeout=timeout)
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - any failure marks the job failed
        logger.exception("report job %s: webhook failed", job_id)
        ReportJob.objects.filter(pk=job_id, status=ReportJob.PENDING).update(
            status=ReportJob.FAILED,
            error=f"Não foi possível acionar o gerador: {exc}",
            finished_at=timezone.now(),
        )
    else:
        ReportJob.objects.filter(pk=job_id, status=ReportJob.PENDING).update(
            status=ReportJob.RUNNING
        )


class _Base(APIView):
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        if not _enabled():
            raise Http404("Geração de relatórios não está habilitada nesta instalação.")
        return super().initial(request, *args, **kwargs)


class ReportJobListCreateView(_Base):
    @swagger_auto_schema(
        tags=["reports"], operation_id="report_jobs_list",
        summary="Lista os relatórios solicitados pelo usuário",
    )
    def get(self, request):
        jobs = [_expire_if_stale(j) for j in ReportJob.objects.filter(
            requested_by=request.user)[:50]]
        return Response({"results": ReportJobSerializer(jobs, many=True).data})

    @swagger_auto_schema(
        tags=["reports"], operation_id="report_jobs_create",
        summary="Dispara a geração de um relatório. Responde 202 imediatamente",
        request_body=ReportJobCreateSerializer,
    )
    def post(self, request):
        ser = ReportJobCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        webhook = getattr(settings, "REPORTS_WEBHOOK_URL", "")
        callback_base = getattr(settings, "REPORTS_CALLBACK_BASE", "")
        if not webhook or not callback_base:
            return Response(
                {"detail": "Gerador não configurado (REPORTS_WEBHOOK_URL / "
                           "REPORTS_CALLBACK_BASE)."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        job = ReportJob.objects.create(
            params=ser.validated_data, requested_by=request.user
        )
        payload = {
            "process_id": ser.validated_data["process_id"],
            "component_ids": ser.validated_data.get("component_ids") or [],
            "log_id": str(job.id),
            # The workflow posts the artefact back here. Sent per-request so the
            # same workflow serves local, staging and production unchanged.
            "backend_url": f"{callback_base.rstrip('/')}/api/reports/jobs/{job.id}/callback/",
        }
        threading.Thread(
            target=_fire_webhook,
            args=(job.id, payload, webhook, int(getattr(settings, "REPORTS_TIMEOUT", 30))),
            daemon=True,
        ).start()
        return Response(ReportJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class ReportJobDetailView(_Base):
    @swagger_auto_schema(
        tags=["reports"], operation_id="report_jobs_detail",
        summary="Estado de um job — a tela consulta isto em intervalos",
    )
    def get(self, request, job_id):
        try:
            job = ReportJob.objects.get(pk=job_id, requested_by=request.user)
        except (ReportJob.DoesNotExist, ValueError, TypeError):
            raise Http404
        return Response(ReportJobSerializer(_expire_if_stale(job)).data)


class ReportJobDownloadView(_Base):
    @swagger_auto_schema(
        tags=["reports"], operation_id="report_jobs_download",
        summary="Baixa o arquivo gerado", produces=["application/octet-stream"],
    )
    def get(self, request, job_id):
        try:
            job = ReportJob.objects.get(pk=job_id, requested_by=request.user)
        except (ReportJob.DoesNotExist, ValueError, TypeError):
            raise Http404
        if job.status != ReportJob.SUCCEEDED or not job.file:
            return Response(
                {"detail": "O relatório ainda não está pronto."},
                status=status.HTTP_409_CONFLICT,
            )
        return FileResponse(
            job.file.open("rb"), as_attachment=True,
            filename=job.filename or f"relatorio_{job.id}.docx",
        )


class ReportCallbackView(APIView):
    """Receives the artefact from n8n. No user session exists on that side,
    so it authenticates with a shared secret, like the KB proxies do."""

    permission_classes = [AllowAny]
    authentication_classes = []
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        tags=["reports"], operation_id="report_jobs_callback",
        summary="Endpoint do n8n para devolver o .docx (multipart, header `key`)",
        manual_parameters=[openapi.Parameter(
            "key", openapi.IN_HEADER, type=openapi.TYPE_STRING, required=True,
            description="Segredo compartilhado (REPORTS_CALLBACK_KEY)")],
    )
    def post(self, request, job_id):
        if not _enabled():
            raise Http404

        expected = getattr(settings, "REPORTS_CALLBACK_KEY", "")
        if not expected or request.headers.get("key") != expected:
            return Response({"detail": "Não autorizado."},
                            status=status.HTTP_401_UNAUTHORIZED)

        try:
            job = ReportJob.objects.get(pk=job_id)
        except (ReportJob.DoesNotExist, ValueError, TypeError):
            raise Http404

        if not job.is_open:
            return Response({"detail": "Job já finalizado.", "status": job.status})

        # The workflow's error branch reports failure the same way.
        failure = request.data.get("error") or request.data.get("status") == "failed"
        upload = request.FILES.get("file")
        if failure or not upload:
            job.status = ReportJob.FAILED
            job.error = str(request.data.get("error") or "O gerador não devolveu arquivo.")
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error", "finished_at"])
            return Response({"detail": "Falha registrada."})

        name = request.data.get("process_name") or ""
        safe = "".join(c for c in str(name) if c.isalnum() or c in " -_")[:60].strip()
        filename = f"relatorio_{safe or job.params.get('process_id', '')}.docx".replace(" ", "_")

        job.file.save(filename, ContentFile(upload.read()), save=False)
        job.filename = filename
        job.size_bytes = job.file.size
        job.process_name = str(name)[:255]
        job.status = ReportJob.SUCCEEDED
        job.finished_at = timezone.now()
        job.save()
        return Response({"detail": "Recebido.", "job": str(job.id), "bytes": job.size_bytes})
