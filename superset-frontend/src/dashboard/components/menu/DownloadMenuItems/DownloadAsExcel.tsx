/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
import { useRef, ChangeEvent } from 'react';
import { logging, t, SupersetClient } from '@superset-ui/core';
import { Menu } from '@superset-ui/core/components/Menu';
import { useSelector } from 'react-redux';
import { LOG_ACTIONS_DASHBOARD_DOWNLOAD_AS_EXCEL } from 'src/logger/LogUtils';
import { useToasts } from 'src/components/MessageToasts/withToasts';
import getFormDataWithExtraFilters from 'src/dashboard/util/charts/getFormDataWithExtraFilters';
import { buildDashboardExportPayload } from 'src/explore/exploreUtils';
import { RootState } from 'src/dashboard/types';


export default function DownloadAsExcel({
  text,
  logEvent,
  dashboardTitle,
  useTemplate = false,
  ...props
}: {
  text: string;
  dashboardTitle: string;
  logEvent?: Function;
  useTemplate?: boolean;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { addDangerToast } = useToasts();
  const chartConfiguration = useSelector(
    (state: RootState) => state.dashboardInfo.metadata?.chart_configuration,
  );
  const colorScheme = useSelector((state: RootState) => state.dashboardState.colorScheme);
  // Color namespace is not used in charts export
  const colorNamespace = undefined;
  const nativeFilters = useSelector((state: RootState) => state.nativeFilters?.filters);
  const dataMask = useSelector((state: RootState) => state.dataMask);
  const charts = useSelector((state: RootState) => state.charts);
  const allSliceIds = useSelector((state: RootState) => state.dashboardState.sliceIds);
  const labelsColor = useSelector(
    (state: RootState) => state.dashboardInfo?.metadata?.label_colors || {},
  );
  const labelsColorMap = useSelector(
    (state: RootState) => state.dashboardInfo?.metadata?.map_label_colors || {},
  );
  const sharedLabelsColors = useSelector((state: RootState) => {
    const colors = state.dashboardInfo?.metadata?.shared_label_colors;
    return typeof colors === 'string' ? JSON.parse(colors) : [];
  });
  const dashboardId = useSelector((state: RootState) => state.dashboardInfo?.id);

  const handleTemplateFileSelected = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    // Validate file type
    if (!file.name.match(/\.(xlsx|xls)$/i)) {
      addDangerToast(t('Please select a valid Excel file'));
      return;
    }

    await performDownload(file);
  };

  const performDownload = async (templateFile?: File) => {
    try {
      // Get form data for each chart on the dashboard
      const rawChartQueries = Object.values(charts).map(chart => {
        if (!chart.form_data) return null;

        const formData = getFormDataWithExtraFilters({
          chart: {
            ...chart,
            formData: chart.form_data,
          },
          filters: {},
          colorScheme,
          colorNamespace,
          sliceId: chart.id,
          nativeFilters,
          dataMask,
          allSliceIds,
          chartConfiguration,
          labelsColor,
          labelsColorMap,
          sharedLabelsColors,
          extraControls: {},
          ownColorScheme: undefined,
        });

        return {
          formData: {
            ...formData,
            chart_name: (chart as any).slice_name || 'Unnamed Chart',
          },
          slice_name: (chart as any).slice_name,
          viz_type: chart.form_data.viz_type,
          datasource: chart.form_data.datasource,
          slice_id: chart.form_data.slice_id,
        };
      }).filter(Boolean);

      if (rawChartQueries.length === 0) {
        addDangerToast(t('No charts found on this dashboard'));
        return;
      }

      // Build query contexts using the utility function
      const payload = buildDashboardExportPayload({
        queries: rawChartQueries,
        force: false,
        resultFormat: 'xlsx',
        resultType: 'full',
      });

      if (!dashboardId) {
        addDangerToast(t('Dashboard ID is required for export'));
        return;
      }

      // Call the dashboard charts export endpoint
      let response;
      const endpoint = `/api/v1/dashboard/${dashboardId}/export_charts_data/`;
      
      if (templateFile) {
        const formData = new FormData();
        formData.append('template', templateFile);
        formData.append('payload', JSON.stringify(payload));
        
        response = await SupersetClient.post({
          endpoint,
          body: formData,
          parseMethod: 'raw',
        });
      } else {
        response = await SupersetClient.post({
          endpoint,
          body: JSON.stringify(payload),
          headers: { 'Content-Type': 'application/json' },
          parseMethod: 'raw',
        });
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${dashboardTitle}_charts.xlsx`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
      logEvent?.(LOG_ACTIONS_DASHBOARD_DOWNLOAD_AS_EXCEL);

    } catch (error) {
      logging.error(error);
      addDangerToast(t('Sorry, something went wrong. Try again later.'));
    }
  };

  return (
    <>
      <input
        type="file"
        accept=".xlsx,.xls"
        onChange={handleTemplateFileSelected}
        ref={fileInputRef}
        style={{ display: 'none' }}
      />
      <Menu.Item
        key="download-excel"
        onClick={() => {
          if (useTemplate) {
            fileInputRef.current?.click();
          } else {
            performDownload();
          }
        }}
        {...props}
      >
        {text}
      </Menu.Item>
    </>
  );
}
