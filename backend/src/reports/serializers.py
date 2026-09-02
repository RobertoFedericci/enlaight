from rest_framework import serializers

from .models import ReportJob


class ReportJobSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = ReportJob
        fields = [
            "id", "status", "error", "params", "filename", "size_bytes",
            "process_name", "created_at", "finished_at", "download_url",
        ]
        read_only_fields = fields

    def get_download_url(self, obj):
        if obj.status != ReportJob.SUCCEEDED or not obj.file:
            return None
        return f"/api/reports/jobs/{obj.id}/download/"


class ReportJobCreateSerializer(serializers.Serializer):
    process_id = serializers.IntegerField(min_value=1)
    component_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, allow_empty=True,
    )
