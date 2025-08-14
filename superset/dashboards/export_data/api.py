"""API endpoints for bulk dashboard data operations"""
import logging
from typing import Any, TYPE_CHECKING

from flask import Response, request
from flask_appbuilder.api import BaseApi, expose, protect, safe
from flask_babel import gettext as _

from superset import app
from superset.extensions import event_logger
from superset.dashboards.commands.export_data import DashboardChartsDataExportCommand
from superset.daos.exceptions import DatasourceNotFound
from superset.exceptions import QueryObjectValidationError
from superset.extensions import event_logger
from superset.models.sql_lab import Query
from superset.models.slice import Slice
from superset.utils import json
from superset.charts.schemas import ChartDataQueryContextSchema
from marshmallow import ValidationError
from superset.common.query_context import QueryContext


from superset.daos.chart import ChartDAO
logger = logging.getLogger(__name__)


class DashboardBulkDataRestApi(BaseApi):
    resource_name = "dashboard"
    allow_browser_login = True
    openapi_spec_tag = "Dashboard"

    @expose("/<dashboard_id>/export_charts_data/", methods=["POST"])
    @protect()
    @safe
    @event_logger.log_this_with_context(
        action=lambda self, *args, **kwargs: f"{self.__class__.__name__}.export_charts_data",
        log_to_statsd=False,
    )
    def export_charts_data(self, dashboard_id: str) -> Response:
        """Export data from all charts in a dashboard using an optional template
        ---
        post:
          description: >-
            Exports data from all charts in a dashboard to Excel, optionally using a template file
          requestBody:
            required: true
            content:
              multipart/form-data:
                schema:
                  type: object
                  properties:
                    template:
                      type: string
                      format: binary
                      description: Optional Excel template file to use as base
                    payload:
                      type: object
                      properties:
                        queries:
                          type: array
                          items:
                            type: object
                            properties:
                              form_data:
                                type: object
              application/json:
                schema:
                  type: object
                  properties:
                    queries:
                      type: array
                      items:
                        type: object
                        properties:
                          form_data:
                            type: object
          responses:
            200:
              description: Charts data export
              content:
                application/excel:
                  schema:
                    type: string
            400:
              $ref: '#/components/responses/400'
            401:
              $ref: '#/components/responses/401'
            500:
              $ref: '#/components/responses/500'
        """
        # Handle both multipart form data (with template) and JSON requests
        json_body = None
        template_file = None
        if request.is_json:
            json_body = request.json
        else:
            if not request.form.get('payload'):
                return self.response_400(message=_("Missing payload parameter"))
            json_body = json.loads(request.form.get('payload'))
            
            if 'template' in request.files:
                template_file = request.files['template']

        if json_body is None:
            return self.response_400(message=_("Request is not JSON"))
        self.json_body: dict[str: Any] = json_body# .get('payload')
        try:
          self._create_query_contexts_and_chart_names()
          command = DashboardChartsDataExportCommand(
              self.query_contexts, 
              dashboard_id=dashboard_id,
              template_file=template_file
          )
          command.validate()
        except DatasourceNotFound:
            return self.response_404()
        except QueryObjectValidationError as error:
            return self.response_400(message=error.message)
        except ValidationError as error:
            return self.response_400(
                message=_(
                    "Request is incorrect: %(error)s", error=error.normalized_messages()
                )
            )
        return command.run()
    
    def _create_query_contexts_and_chart_names(self) -> None:
        self.query_contexts: list[dict] = []
        # logger.info(json_body.get('queries'))#[0])
        for chart_json in self.json_body.get('queries'):
              chart_data: dict[str, Any] = {}
              slice_id: int = chart_json.get('form_data').get('slice_id')
              chart_name: str = 'Unnamed Chart'
              if slice_id:
                  chart: Slice = ChartDAO.find_by_id(slice_id) 
                  if chart:
                      chart_name = chart.slice_name
              chart_data['chart_name'] = chart_name
              chart_data['query_context'] = self._create_query_context_from_form(chart_json)
              self.query_contexts.append(chart_data)

    # @logs_context(context_func=_map_form_data_datasource_to_dataset_id)
    def _create_query_context_from_form(
        self, form_data: dict[str, Any]
    ) -> QueryContext:
        """
        Create the query context from the form data.

        :param form_data: The chart form data
        :returns: The query context
        :raises ValidationError: If the request is incorrect
        """
        try:
            return ChartDataQueryContextSchema().load(form_data)
        except KeyError as ex:
            raise ValidationError("Request is incorrect") from ex
        except Exception as e:
            logger.info(f'exception {e}')
            raise e
