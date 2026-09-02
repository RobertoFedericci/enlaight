from django.urls import path

from .views import (
    ComponentListView,
    ExportView,
    FacetsView,
    ProcessListView,
    ProposalListView,
)

urlpatterns = [
    path("processes/", ProcessListView.as_view(), name="proposals_processes"),
    path(
        "processes/<int:process_id>/components/",
        ComponentListView.as_view(),
        name="proposals_components",
    ),
    path("facets/", FacetsView.as_view(), name="proposals_facets"),
    path("export/", ExportView.as_view(), name="proposals_export"),
    path("", ProposalListView.as_view(), name="proposals_search"),
]
