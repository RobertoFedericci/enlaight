from django.urls import path

from .views import (
    ReportCallbackView,
    ReportJobDetailView,
    ReportJobDownloadView,
    ReportJobListCreateView,
)

urlpatterns = [
    path("jobs/", ReportJobListCreateView.as_view(), name="report_jobs"),
    path("jobs/<uuid:job_id>/", ReportJobDetailView.as_view(), name="report_job_detail"),
    path("jobs/<uuid:job_id>/download/", ReportJobDownloadView.as_view(), name="report_job_download"),
    path("jobs/<uuid:job_id>/callback/", ReportCallbackView.as_view(), name="report_job_callback"),
]
