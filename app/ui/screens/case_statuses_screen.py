"""Case statuses module screen (إدارة الحالات).

UI only: reuses :class:`BaseCrudScreen` bound to the ``case_statuses`` table spec.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from app.services.review_data_service import ReviewDataService, TABLE_SPECS
from app.ui.screens.base_crud_screen import BaseCrudScreen


class CaseStatusesScreen(BaseCrudScreen):
    SPEC_KEY = "case_statuses"

    def __init__(self, service: ReviewDataService, parent: QWidget | None = None) -> None:
        super().__init__(service, TABLE_SPECS[self.SPEC_KEY], parent)
