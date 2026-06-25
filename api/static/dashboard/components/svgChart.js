/**
 * svgChart.js — Accessible SVG bar chart with screen-reader table fallback.
 *
 * @param {{ series: number[], labels: string[], title: string }} opts
 * @returns {HTMLElement} .chart-container wrapping <svg> + .visually-hidden <table>
 *
 * All data is inserted via textContent / setAttribute — no innerHTML of data.
 * The <svg> has role="img" + aria-label summarising the data.
 * The <table> duplicate gives screen readers and no-JS users full access.
 */

const SVG_NS = 'http://www.w3.org/2000/svg';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function svgEl(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    node.setAttribute(k, String(v));
  }
  return node;
}

function svgText(node, str) {
  node.textContent = String(str);
  return node;
}

// ---------------------------------------------------------------------------
// Accessible table fallback (screen-reader / no-JS)
// ---------------------------------------------------------------------------

function buildTable(labels, series, title) {
  const table = document.createElement('table');
  table.className = 'visually-hidden';
  table.setAttribute('aria-label', title);

  const caption = document.createElement('caption');
  caption.textContent = title;
  table.appendChild(caption);

  // <thead>
  const thead = document.createElement('thead');
  const headerRow = document.createElement('tr');

  const thLabel = document.createElement('th');
  thLabel.setAttribute('scope', 'col');
  thLabel.textContent = 'Period';
  headerRow.appendChild(thLabel);

  const thValue = document.createElement('th');
  thValue.setAttribute('scope', 'col');
  thValue.textContent = 'Value';
  headerRow.appendChild(thValue);

  thead.appendChild(headerRow);
  table.appendChild(thead);

  // <tbody>
  const tbody = document.createElement('tbody');
  labels.forEach((label, i) => {
    const tr = document.createElement('tr');

    const tdLabel = document.createElement('td');
    tdLabel.textContent = String(label);
    tr.appendChild(tdLabel);

    const tdValue = document.createElement('td');
    tdValue.textContent = String(series[i] != null ? series[i] : 0);
    tr.appendChild(tdValue);

    tbody.appendChild(tr);
  });
  table.appendChild(tbody);

  return table;
}

// ---------------------------------------------------------------------------
// SVG chart
// ---------------------------------------------------------------------------

function buildSvg(labels, series, title) {
  const count = labels.length;
  if (count === 0) return svgEl('svg', { viewBox: '0 0 0 0' });

  // Layout constants (unitless — SVG viewBox coords)
  const W = 400;
  const H = 200;
  const PAD_LEFT = 40;
  const PAD_RIGHT = 16;
  const PAD_TOP = 16;
  const PAD_BOTTOM = 36; // room for labels

  const chartW = W - PAD_LEFT - PAD_RIGHT;
  const chartH = H - PAD_TOP - PAD_BOTTOM;

  const maxVal = Math.max(...series.map(Number), 1); // prevent /0

  // aria-label summary
  const summary =
    title +
    ': ' +
    labels.map((l, i) => l + ' ' + (series[i] != null ? series[i] : 0)).join(', ');

  const svg = svgEl('svg', {
    viewBox: '0 0 ' + W + ' ' + H,
    role: 'img',
    'aria-label': summary,
    focusable: 'false', // IE compat; modern browsers ignore
    xmlns: SVG_NS,
  });

  // <title> inside SVG for additional AT exposure
  const titleEl = svgEl('title', {});
  svgText(titleEl, title);
  svg.appendChild(titleEl);

  // Background axis lines (horizontal grid)
  const gridCount = 4;
  for (let g = 0; g <= gridCount; g++) {
    const y = PAD_TOP + (chartH / gridCount) * g;
    const gridLine = svgEl('line', {
      x1: PAD_LEFT,
      y1: y,
      x2: PAD_LEFT + chartW,
      y2: y,
      stroke: '#E5E7EB',
      'stroke-width': '1',
      'stroke-dasharray': g === gridCount ? 'none' : '4 2',
    });
    svg.appendChild(gridLine);

    // Y-axis tick value
    if (g < gridCount) {
      const tickVal = Math.round(maxVal - (maxVal / gridCount) * g);
      const tickLabel = svgEl('text', {
        x: PAD_LEFT - 6,
        y: y + 4,
        'text-anchor': 'end',
        'font-size': '10',
        fill: '#6B7280',
        'aria-hidden': 'true',
      });
      svgText(tickLabel, String(tickVal));
      svg.appendChild(tickLabel);
    }
  }

  // Bars + x-axis labels
  const barGroupW = chartW / count;
  const barPad = Math.max(4, barGroupW * 0.18);
  const barW = Math.max(4, barGroupW - barPad * 2);

  series.forEach((rawVal, i) => {
    const val = Number(rawVal) || 0;
    const barH = (val / maxVal) * chartH;
    const x = PAD_LEFT + i * barGroupW + barPad;
    const y = PAD_TOP + chartH - barH;

    // Bar rect
    const rect = svgEl('rect', {
      x: x,
      y: y,
      width: barW,
      height: barH,
      fill: '#6366F1', // indigo-500, matches --color-primary family
      rx: '3',
      ry: '3',
      'aria-hidden': 'true',
    });
    svg.appendChild(rect);

    // X-axis label
    const labelEl = svgEl('text', {
      x: x + barW / 2,
      y: H - PAD_BOTTOM + 16,
      'text-anchor': 'middle',
      'font-size': '10',
      fill: '#6B7280',
      'aria-hidden': 'true',
    });
    svgText(labelEl, String(labels[i]));
    svg.appendChild(labelEl);

    // Value label above bar (only if bar tall enough)
    if (barH >= 16) {
      const valueLabel = svgEl('text', {
        x: x + barW / 2,
        y: y - 4,
        'text-anchor': 'middle',
        'font-size': '9',
        fill: '#374151',
        'aria-hidden': 'true',
      });
      svgText(valueLabel, String(val));
      svg.appendChild(valueLabel);
    }
  });

  return svg;
}

// ---------------------------------------------------------------------------
// Public export
// ---------------------------------------------------------------------------

/**
 * @param {{ series: number[], labels: string[], title: string }} opts
 * @returns {HTMLElement}
 */
export function svgChart({ series, labels, title }) {
  const safeLabels = Array.isArray(labels) ? labels : [];
  const safeSeries = Array.isArray(series) ? series : [];
  const safeTitle = String(title || 'Chart');

  const container = document.createElement('div');
  container.className = 'chart-container';

  container.appendChild(buildSvg(safeLabels, safeSeries, safeTitle));
  container.appendChild(buildTable(safeLabels, safeSeries, safeTitle));

  return container;
}
