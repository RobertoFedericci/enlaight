"""Asynchronous document-generation jobs.

The generator is an n8n workflow that takes 90-200 s to run, so the request
that starts it cannot also return the artefact: any reverse proxy in front of
the platform would cut the connection first. The job row is the handle — the
UI polls it, and the workflow posts the finished file back to the callback.
"""

import uuid

from django.conf import settings
from django.db import models


class ReportJob(models.Model):
    PENDING, RUNNING, SUCCEEDED, FAILED = "pending", "running", "succeeded", "failed"
    STATUS_CHOICES = [
        (PENDING, "pending"),
        (RUNNING, "running"),
        (SUCCEEDED, "succeeded"),
        (FAILED, "failed"),
    ]

    # Travels to the workflow as log_id and comes back on the callback.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    params = models.JSONField(default=dict)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING)
    error = models.TextField(blank=True)

    file = models.FileField(upload_to="reports/%Y/%m/", null=True, blank=True)
    filename = models.CharField(max_length=255, blank=True)
    size_bytes = models.BigIntegerField(null=True, blank=True)
    process_name = models.CharField(max_length=255, blank=True)

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="report_jobs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "report_jobs"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.id} [{self.status}]"

    @property
    def is_open(self):
        return self.status in (self.PENDING, self.RUNNING)
