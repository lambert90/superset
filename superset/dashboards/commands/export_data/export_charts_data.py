"""Export data from all charts in a dashboard"""
import logging
import json
import os
from typing import Any, Dict, List, Optional, Tuple
from flask_babel import lazy_gettext as _
import pandas as pd
from flask import make_response
from io import BytesIO
from superset.commands.base import BaseCommand
from superset.commands.chart.data.get_data_command import ChartDataCommand
from superset.models.dashboard import Dashboard
from superset.utils.pandas_to_named_range import write_df_to_named_range
from superset.common.query_context import QueryContext

logger = logging.getLogger(__name__)


class DashboardChartsDataExportCommand(BaseCommand):
    
    _query_contexts: list[QueryContext]
    TEMPLATE_DIR = os.path.join(
        os.path.dirname(
            os.path.dirname(
                os.path.dirname(
                    os.path.dirname(__file__)))), 
                               "excel_templates")
    CONFIG_FILE_PATH = os.path.join(TEMPLATE_DIR, "dashboard_configuration.json")
    used_names: Dict[str, int] = {}

    def __init__(self, chart_data: List[Dict[str, Any]], dashboard_id: Optional[str] = None, template_file: BytesIO = None):
        self.chart_data: List[Dict[str, Any]] = chart_data
        self.dashboard_id: str = dashboard_id
        self.template_file: BytesIO = template_file
        self._query_contexts: List[QueryContext] = [i.get("query_context") for i in self.chart_data]

        if dashboard_id:
            self._find_dashboard_template()

    def run(self, **kwargs: Any) -> dict[str, Any]:
        all_data: List[Dict[str, Any]] = []
        metadata: List[Dict[str, Any]] = []

        for chart in self.chart_data:
            all_data_element, metadata_element = self._extract_chart_data(chart)
            all_data.append(all_data_element)
            metadata.append(metadata_element)

        # Create Excel file with multiple sheets
        output: BytesIO = BytesIO()

        if self.template_file:
            # Use template file if provided
            self._write_chart_data_to_template(all_data, metadata, output)
                
        else:
            # Standard export without template
            self._write_chart_data_to_new_excel_file(all_data, metadata, output)

        output.seek(0)
        response = make_response(output.read())
        response.headers["Content-Disposition"] = "attachment; filename=dashboard_export.xlsx"
        response.headers["Content-Type"] = "application/vnd.ms-excel"
        return response

    def _write_chart_data_to_new_excel_file(self, all_data, metadata, output):
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            # Write data sheets
            for single_chart_data in all_data:
                sheet_name = self._generate_sheet_name(single_chart_data["sanitised_name"])
                single_chart_data["data"].to_excel(writer, sheet_name=sheet_name, index=False)
            # Write metadata sheet
            pd.DataFrame(metadata).to_excel(writer, sheet_name="Export Metadata", index=False)

    def _write_chart_data_to_template(self, all_data, metadata, output):
        try:
            # Copy template to output
            self.template_file.seek(0)
            output.write(self.template_file.read())
            output.seek(0)
                
            # Open existing workbook
            with pd.ExcelWriter(output, engine='openpyxl', mode='a', if_sheet_exists='overlay') as writer:
                named_range_names = [name for name in writer.book.defined_names]
                # Write data sheets
                for single_chart_data in all_data:
                    # Try to write to named range if configuration exists
                    if single_chart_data['sanitised_name'] in named_range_names:

                        df_without_blank_column = single_chart_data['data'].drop('', axis=1)
                        write_df_to_named_range(
                                df=df_without_blank_column,
                                wb=writer.book,
                                named_range=single_chart_data['sanitised_name'],
                                include_header=False
                            )
                    else:
                        sheet_name = self._generate_sheet_name(single_chart_data["sanitised_name"])
                        single_chart_data["data"].to_excel(writer, sheet_name=sheet_name, index=False)
            
                    # Write metadata sheet
                pd.DataFrame(metadata).to_excel(writer, sheet_name="Export Metadata", index=False)
                    
        except Exception as e:
            logger.error(f"Error using template file: {str(e)}")
            raise ValueError(f"Failed to use template file: {str(e)}")

    def _extract_chart_data(self, chart: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        
        chart_name: str = chart.get("chart_name")
        query_context: QueryContext = chart.get("query_context")
        try:
            chart_data: dict[str, Any] = ChartDataCommand(query_context).run()
            if not chart_data:
                logger.error(f'No result returned from ChartDataCommand for chart: {chart_name}')
                raise ValueError("No result from chart data command")
            
            if not chart_data.get("queries"):
                logger.error(f'No queries in result for chart: {chart_name} Result: {chart_data}')
                raise ValueError("No queries in result")
            
            query_result = chart_data["queries"][0]

            if not query_result.get("data"):
                raise ValueError(f"No data returned for chart: {chart_name}")
            
            try:
                if isinstance(query_result["data"], bytes):
                    # If we get binary data, assume it's already an Excel file
                    import io
                    excel_data = io.BytesIO(query_result["data"])
                    # Read all sheets from the Excel file
                    df = pd.read_excel(excel_data, sheet_name=None)
                    # If multiple sheets, take the first one
                    if isinstance(df, dict):
                        df = next(iter(df.values()))
                else:
                    # If not bytes, assume it's JSON-compatible data
                    df = pd.DataFrame(query_result["data"])
                
            except Exception as e:
                logger.error(f"Failed to create DataFrame: {str(e)}")
                logger.error(f"Raw data type: {type(query_result['data'])}")
                raise

            # hacky fudge to handle renaming Index
            if 'Unnamed: 0' in list(df.columns):
                df.rename(columns={'Unnamed: 0': ''}, inplace=True)

            chart_name = chart_name.strip()
            all_data_element = {
                "name": chart_name,
                "sanitised_name": "".join(c if c.isalnum() or c in " -" else "_" for c in chart_name).strip(),
                "data": df
                }
            metadata_element = {
                "chart_name": chart_name,
                "status": "success",
                "error": None,
                "columns": list(df.columns),
                "row_count": len(df)
            }
        except Exception as e:
            error_msg = str(e)
            logger.error(f'Error processing chart: {chart_name}')
            logger.error(f'Error details: {error_msg}')
            logger.error(f'Chart request data that caused error: {chart}')
            metadata_element = {
                "chart_name": chart_name,
                "status": "error", 
                "error": error_msg
            }
            return pd.DataFrame(), metadata_element
        return all_data_element, metadata_element

    def _find_dashboard_template(self) -> None:
        """Load dashboard template based on dashboard name"""
        dashboard = Dashboard.get(self.dashboard_id)
        if not dashboard:
            logger.warning(f"Dashboard not found with id {self.dashboard_id}")
            return
        dashboard_name = dashboard.dashboard_title
        list_templates = [file for file in os.listdir(self.TEMPLATE_DIR) if f'{dashboard_name}.xlsx' in file]
        if len(list_templates) == 0:
            logger.info(f'No template available for dashboard: {dashboard_name}')
            return 
        logger.info(f'template exists. Using template: {list_templates[0]}')
        
        template_path = os.path.join(self.TEMPLATE_DIR, list_templates[0])
        if not os.path.exists(template_path):
            logger.error(f"Template file not found: {template_path}")
            return
            
        with open(template_path, 'rb') as f:
            self.template_file = BytesIO(f.read())

    def _write_chart_to_template(self, chart_data: Dict[str, Any], writer: pd.ExcelWriter) -> Tuple[bool, str]:
        """
        Attempt to write chart data to template using named ranges.
        Returns (success, sheet_name)
        """
        if not self.dashboard_config or not self.dashboard_config.get('charts_mapping'):
            return False, ""
            
        chart_name = chart_data['name']
        chart_config = None
        for entry in self.dashboard_config['charts_mapping']:
            test_name = entry.get('chart_name')

            if entry.get('chart_name') == chart_name:
                chart_config = entry
                break

        if not chart_config:
            return False, ""
            
        try:
            named_range = chart_config['named_range_mapping']
            include_headers = chart_config.get('include_header', True)
            
            # Load workbook from writer
            workbook = writer.book
            
            # Try to write to named range
            write_df_to_named_range(
                chart_data['data'],
                workbook,
                named_range,
                include_header=include_headers
            )
            
            return True, ""  # Success, no sheet name needed
            
        except ValueError as ve:
            # Named range not found
            logger.error(f"Named range error for chart {chart_name}: {str(ve)}")
            return False, self._generate_sheet_name(chart_name)
            
        except Exception as e:
            # Other errors (e.g., data size)
            logger.error(f"Error writing chart {chart_name} to template: {str(e)}")
            return False, self._generate_sheet_name(chart_name)

    def _generate_sheet_name(self, sheet_name: str) -> str:
        """Generate a valid Excel sheet name"""
        
        # Handle duplicate names
        base = sheet_name[:28]  # Leave room for suffix
        if base in self.used_names:
            self.used_names[base] += 1
            sheet_name = f"{base}_{self.used_names[base]}"
        else:
            self.used_names[base] = 0
        
        # Ensure valid name
        if not sheet_name:
            sheet_name = f"Chart_{len(self.used_names)}"
        return sheet_name[:31]

    def validate(self) -> None:
        for _query_context in self._query_contexts:
            _query_context.raise_for_access()
