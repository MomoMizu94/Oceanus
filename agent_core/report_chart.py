""" Build bounded charts from recomputed CSV aggregates """
from datetime import date
from decimal import Decimal
from io import BytesIO
import json
import math
import re
import textwrap


MAX_CHART_BYTES = 8 * 1024 * 1024


def chart_text(value, label, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{label} must contain 1 to {limit} characters.')
    if any(ord(character) < 32 for character in value):
        raise ValueError(f'{label} must not contain control characters.')
    return value


def build_chart_spec(summary, options):
    keys = {'column', 'currency', 'grouping', 'kind', 'title', 'x_label', 'y_label'}
    if not isinstance(options, dict) or set(options) != keys:
        raise ValueError('Invalid chart options.')
    column = options['column']
    if type(column) is not int:
        raise ValueError('Chart column must be a zero-based integer.')
    measure = None
    for candidate in summary['measures']:
        if candidate['column'] == column:
            measure = candidate
            break
    if measure is None:
        raise ValueError('Chart column was not selected in the analysis.')
    currency = options['currency']
    if not isinstance(currency, str) or currency not in measure['totals']:
        raise ValueError('Choose one currency/unit present in the selected measure.')
    grouping = options['grouping']
    kind = options['kind']
    if grouping not in ('group', 'month') or kind not in ('bar', 'line'):
        raise ValueError('Choose group/month grouping and bar/line chart kind.')
    if kind == 'line' and grouping != 'month':
        raise ValueError('Line charts require chronological month grouping.')
    for key in ('title', 'x_label', 'y_label'):
        chart_text(options[key], key, 120)
    points = []
    for label, values in measure['by_' + grouping].items():
        if currency in values:
            points.append({'label': label, 'value': values[currency]})
    if not points:
        raise ValueError('No recorded values for that currency and grouping.')
    if grouping == 'month':
        values = {point['label']: point['value'] for point in points}
        first = date.fromisoformat(summary['date_min'])
        last = date.fromisoformat(summary['date_max'])
        first_month = first.year * 12 + first.month - 1
        last_month = last.year * 12 + last.month - 1
        if last_month - first_month >= 120:
            raise ValueError('Chart covers more than 120 calendar months; narrow the input.')
        points = []
        for month_index in range(first_month, last_month + 1):
            year, month = divmod(month_index, 12)
            label = f'{year:04d}-{month + 1:02d}'
            points.append({'label': label, 'value': values.get(label)})
    axis_column = summary['options']['group_column' if grouping == 'group' else 'date_column']
    basis = 'All records' if axis_column is None else summary['headers'][axis_column]
    spec = {
        **options, 'points': points, 'measure': measure['header'], 'basis': basis,
        'verification': summary['verification']['status'], 'missing': measure['missing'],
        'source_sha256': summary['source_sha256'],
    }
    validate_chart_spec(spec)
    return spec


def validate_chart_spec(spec):
    keys = {'column', 'currency', 'grouping', 'kind', 'title', 'x_label', 'y_label',
            'points', 'measure', 'basis', 'verification', 'missing', 'source_sha256'}
    if not isinstance(spec, dict) or set(spec) != keys:
        raise ValueError('Invalid chart specification.')
    if type(spec['column']) is not int or spec['column'] < 0:
        raise ValueError('Invalid measure column.')
    if spec['grouping'] not in ('group', 'month') or spec['kind'] not in ('bar', 'line'):
        raise ValueError('Invalid chart kind or grouping.')
    if spec['kind'] == 'line' and spec['grouping'] != 'month':
        raise ValueError('Line charts require months.')
    for key in ('title', 'x_label', 'y_label', 'measure', 'basis'):
        chart_text(spec[key], key, 120)
    currency = spec['currency']
    if not isinstance(currency, str) or re.fullmatch(r'[A-Z]{3}|unspecified', currency) is None:
        raise ValueError('Invalid currency/unit.')
    if spec['verification'] not in ('passed', 'failed', 'unresolved'):
        raise ValueError('Invalid verification status.')
    if type(spec['missing']) is not int or spec['missing'] < 0:
        raise ValueError('Invalid missing count.')
    if not isinstance(spec['source_sha256'], str) or re.fullmatch(r'[0-9a-f]{64}', spec['source_sha256']) is None:
        raise ValueError('Invalid source hash.')
    points = spec['points']
    maximum = 30 if spec['grouping'] == 'group' else 120
    if not isinstance(points, list) or not 1 <= len(points) <= maximum:
        raise ValueError(f'Chart requires 1 to {maximum} points.')
    seen = set()
    values = []
    wrapped_lines = 0
    previous_month = None
    for point in points:
        if not isinstance(point, dict) or set(point) != {'label', 'value'}:
            raise ValueError('Invalid chart point.')
        label = chart_text(point['label'], 'Point label', 120)
        if label in seen:
            raise ValueError('Duplicate chart label.')
        seen.add(label)
        wrapped_lines += len(textwrap.wrap(label, 28))
        if spec['grouping'] == 'month':
            if re.fullmatch(r'[0-9]{4}-[0-9]{2}', label) is None:
                raise ValueError('Month labels must use YYYY-MM.')
            month = date.fromisoformat(label + '-01')
            index = month.year * 12 + month.month - 1
            if previous_month is not None and index != previous_month + 1:
                raise ValueError('Month points must be consecutive; use null for gaps.')
            previous_month = index
        value = point['value']
        if value is None and spec['grouping'] == 'month':
            values.append(float('nan'))
            continue
        if not isinstance(value, str) or re.fullmatch(r'[+-]?[0-9]+(?:\.[0-9]+)?', value) is None or len(value) > 256:
            raise ValueError('Chart values must be decimal strings.')
        exact = Decimal(value)
        plotted = float(exact)
        if not math.isfinite(plotted) or (exact != 0 and (plotted == 0 or abs(plotted) < 1e-100 or abs(plotted) > 1e100)):
            raise ValueError('Value is outside the supported plotting range.')
        values.append(plotted)
    if spec['grouping'] == 'group' and wrapped_lines > 60:
        raise ValueError('Group labels need too much space; narrow the input or choose monthly grouping.')
    observed = sum(math.isfinite(value) for value in values)
    if observed < (2 if spec['kind'] == 'line' else 1):
        raise ValueError('Not enough recorded values for the selected chart kind.')
    return values


def render_chart_png(spec):
    values = validate_chart_spec(spec)
    from matplotlib import rc_context
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    labels = [point['label'] for point in spec['points']]
    horizontal = spec['grouping'] == 'group'
    wrapped_labels = [textwrap.fill(label, 28) for label in labels]
    height = 6
    if horizontal:
        height = max(6, 2 + sum(label.count('\n') + 1 for label in wrapped_labels) * 0.3)
    with rc_context({'text.usetex': False, 'text.parse_math': False, 'font.family': 'DejaVu Sans',
                     'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False}):
        figure = Figure(figsize=(11, height), dpi=150, layout='constrained')
        FigureCanvasAgg(figure)
        axis = figure.add_subplot()
        positions = list(range(len(values)))
        colors = ['#b94b42' if value < 0 else '#287fa3' for value in values]
        if horizontal:
            axis.barh(positions, values, color=colors)
            axis.set_yticks(positions, wrapped_labels)
            axis.invert_yaxis()
            axis.axvline(0, color='#444444', linewidth=0.8)
            axis.grid(axis='x', alpha=0.2)
        else:
            if spec['kind'] == 'line':
                axis.plot(positions, values, color='#287fa3', marker='o', markersize=3)
            else:
                axis.bar(positions, values, color=colors)
            step = max(1, math.ceil(len(labels) / 12))
            ticks = list(range(0, len(labels), step))
            if ticks[-1] != len(labels) - 1:
                ticks.append(len(labels) - 1)
            axis.set_xticks(ticks, [labels[index] for index in ticks], rotation=45, ha='right')
            axis.axhline(0, color='#444444', linewidth=0.8)
            low, high = axis.get_ylim()
            axis.set_ylim(min(0, low), max(0, high))
            axis.grid(axis='y', alpha=0.2)
        axis.set_axisbelow(True)
        measure_label = f"{spec['measure']} [{spec['currency']}]"
        axis.set_xlabel(textwrap.fill(spec['x_label'], 70) + '\n' + (measure_label if horizontal else spec['basis']))
        axis.set_ylabel(textwrap.fill(spec['y_label'], 70) + '\n' + (spec['basis'] if horizontal else measure_label))
        axis.set_title(textwrap.fill(spec['title'], 75), loc='left', pad=18, weight='bold')
        note = (f"As-listed values | Verification: {spec['verification'].upper()} | "
                f"Missing values in measure: {spec['missing']}\n"
                'One currency/unit; blanks excluded. Missing months are gaps, not zero. '
                'Chart geometry uses rounded plotting coordinates.')
        figure.supxlabel(note, fontsize=8)
        output = BytesIO()
        figure.savefig(output, format='png', metadata={'Description': json.dumps(spec, ensure_ascii=True)})
    png = output.getvalue()
    if len(png) > MAX_CHART_BYTES:
        raise ValueError('Chart exceeds the 8 MiB image limit.')
    return png
