""" Bounded CSV parsing and exact aggregates without financial interpretations """
import csv
import hashlib
from html import escape
import io
import json
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, Inexact, Rounded, localcontext


MAX_CSV_BYTES = 10 * 1024 * 1024
MAX_CSV_ROWS = 50_000
MAX_SUMMARY_CHARS = 200_000
DELIMITERS = {'comma': ',', 'semicolon': ';', 'tab': '\t', 'pipe': '|'}


def check_cancelled(should_cancel):
    if should_cancel is not None and should_cancel():
        raise InterruptedError('CSV analysis cancelled before publication.')


def csv_format_error(reason: str, text: str, delimiter: str) -> ValueError:
    message = f'{reason} Selected delimiter: {delimiter}.'
    try:
        # A bounded sample can suggest a retry, but never changes parsing
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;\t|')
    except csv.Error:
        dialect = None
    if dialect is not None:
        for name, character in DELIMITERS.items():
            if character == dialect.delimiter and name != delimiter:
                message += f' Possible delimiter: {name}.'
                break
    message += ' Inspect the header and a few records with read_file_lines before retrying with an explicit delimiter.'
    return ValueError(message)


def parse_csv(raw: bytes, delimiter: str, should_cancel=None) -> tuple:
    if not isinstance(raw, bytes) or len(raw) > MAX_CSV_BYTES:
        raise ValueError('CSV source exceeds the 10 MiB limit.')
    if delimiter not in DELIMITERS:
        raise ValueError('Choose comma, semicolon, tab, or pipe explicitly.')
    if raw.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
        raise ValueError('UTF-32 is unsupported.')
    encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    text = raw.decode(encoding)
    if '\x00' in text:
        raise ValueError('CSV contains NUL characters.')
    reader = csv.reader(io.StringIO(text, newline=''), delimiter=DELIMITERS[delimiter], strict=True)
    try:
        header = next(reader, [])
        if not 1 <= len(header) <= 200 or any(not h.strip() or len(h) > 120 for h in header):
            raise csv_format_error('CSV requires 1..200 nonblank headers of at most 120 characters.', text, delimiter)
        rows = []
        for row in reader:
            check_cancelled(should_cancel)
            if not row:
                continue
            if len(row) != len(header):
                raise csv_format_error(f'Record {len(rows) + 2}: field count does not match header.', text, delimiter)
            if len(rows) >= MAX_CSV_ROWS:
                raise ValueError('CSV exceeds 50,000 records.')
            rows.append(row)
    except csv.Error as error:
        raise csv_format_error('Malformed CSV or oversized field.', text, delimiter) from error
    return encoding, header, rows


def parse_decimal(value: str, separator: str) -> Decimal:
    text = value.strip()
    pattern = r'[+-]?[0-9]+(?:' + re.escape(separator) + r'[0-9]+)?'
    if len(text) > 100 or re.fullmatch(pattern, text) is None:
        raise ValueError('Invalid decimal; use the selected decimal separator without thousands separators.')
    return Decimal(text.replace(separator, '.'))


def validate_options(options: dict, width: int) -> None:
    keys = {'delimiter', 'decimal_separator', 'group_column', 'date_column', 'measures', 'expected_totals'}
    if not isinstance(options, dict) or set(options) != keys:
        raise ValueError('Invalid CSV analysis options.')
    if options['delimiter'] not in DELIMITERS or options['decimal_separator'] not in ('.', ','):
        raise ValueError('Invalid delimiter or decimal separator.')

    def column(index, nullable=False):
        if index is None and nullable:
            return
        if type(index) is not int or not 0 <= index < width:
            raise ValueError('Column indexes must be zero-based integers within the header.')

    column(options['group_column'], True)
    column(options['date_column'], True)
    measures = options['measures']
    if not isinstance(measures, list) or not 1 <= len(measures) <= 8:
        raise ValueError('Select 1..8 numeric measures.')
    selected = set()
    for measure in measures:
        if not isinstance(measure, dict) or set(measure) != {'column', 'currency_column'}:
            raise ValueError('Each measure needs column and currency_column.')
        column(measure['column'])
        column(measure['currency_column'], True)
        if measure['column'] in selected or measure['column'] == measure['currency_column']:
            raise ValueError('Measure columns must be unique and differ from their currency column.')
        selected.add(measure['column'])
    expectations = options['expected_totals']
    if not isinstance(expectations, list) or len(expectations) > 32:
        raise ValueError('Provide at most 32 independently supplied expected totals.')
    seen = set()
    for expectation in expectations:
        if not isinstance(expectation, dict) or set(expectation) != {'column', 'currency', 'total'}:
            raise ValueError('Expected totals need column, currency, and total.')
        column(expectation['column'])
        if expectation['column'] not in selected:
            raise ValueError('Expected total refers to an unselected measure.')
        currency = expectation['currency']
        if not isinstance(currency, str) or re.fullmatch(r'[A-Z]{3}|unspecified', currency) is None:
            raise ValueError('Expected currency must be three uppercase letters or unspecified.')
        if not isinstance(expectation['total'], str):
            raise ValueError('Expected totals must be decimal strings with a dot separator.')
        parse_decimal(expectation['total'], '.')
        key = (expectation['column'], currency)
        if key in seen:
            raise ValueError('Duplicate expected total.')
        seen.add(key)


def _strings(totals):
    return {key: format(value, 'f') for key, value in sorted(totals.items())}


def _check(name, status, detail):
    return {'name': name, 'status': status, 'detail': detail}


def analyze(raw: bytes, options: dict, should_cancel=None) -> dict:
    if not isinstance(options, dict):
        raise ValueError('CSV options must be an object.')
    encoding, header, rows = parse_csv(raw, options.get('delimiter'), should_cancel)
    validate_options(options, len(header))
    dates = []
    groups = []
    for number, row in enumerate(rows, 2):
        check_cancelled(should_cancel)
        group = 'All records' if options['group_column'] is None else row[options['group_column']].strip()
        if not group or len(group) > 200:
            raise ValueError(f'Record {number}: group is blank or exceeds 200 characters.')
        groups.append(group)
        value = None
        if options['date_column'] is not None:
            text = row[options['date_column']].strip()
            if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', text) is None:
                raise ValueError(f'Record {number}: date must use YYYY-MM-DD.')
            value = date.fromisoformat(text).isoformat()
        dates.append(value)
    if len(set(groups)) > 100:
        raise ValueError('More than 100 groups; choose a less detailed group column.')
    group_counts = defaultdict(int)
    for group in groups:
        group_counts[group] += 1
    checks = [_check('input_structure', 'passed', f'Parsed all {len(rows)} records without dropping malformed rows.')]
    measures = []
    with localcontext() as context:
        # Two bounded decimals plus the row count fit without rounding
        context.prec = 256 + len(str(len(rows)))
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        for definition in options['measures']:
            index = definition['column']
            currency_index = definition['currency_column']
            totals = defaultdict(Decimal)
            by_group = defaultdict(lambda: defaultdict(Decimal))
            by_month = defaultdict(lambda: defaultdict(Decimal))
            counts_by_group = {}
            for group in sorted(group_counts):
                counts_by_group[group] = {'positive': 0, 'zero': 0, 'negative': 0, 'missing': 0}
            missing = negative = positive = zero = present = 0
            negative_dates = set()
            for offset, row in enumerate(rows):
                check_cancelled(should_cancel)
                group_counts_for_measure = counts_by_group[groups[offset]]
                text = row[index].strip()
                if not text:
                    missing += 1
                    group_counts_for_measure['missing'] += 1
                    continue
                try:
                    amount = parse_decimal(text, options['decimal_separator'])
                except ValueError as error:
                    raise ValueError(f'Record {offset + 2}, column {index}: {error}') from error
                currency = 'unspecified' if currency_index is None else row[currency_index].strip()
                if currency_index is not None and re.fullmatch(r'[A-Z]{3}', currency) is None:
                    raise ValueError(f'Record {offset + 2}: missing or invalid currency for column {index}.')
                totals[currency] += amount
                if len(totals) > 20:
                    raise ValueError('More than 20 currency labels in one measure.')
                by_group[groups[offset]][currency] += amount
                if dates[offset] is not None:
                    by_month[dates[offset][:7]][currency] += amount
                    if len(by_month) > 240:
                        raise ValueError('More than 240 months; narrow the input period.')
                present += 1
                if amount < 0:
                    negative += 1
                    group_counts_for_measure['negative'] += 1
                    if dates[offset] is not None:
                        negative_dates.add(dates[offset])
                elif amount > 0:
                    positive += 1
                    group_counts_for_measure['positive'] += 1
                else:
                    zero += 1
                    group_counts_for_measure['zero'] += 1
            reconciled = defaultdict(Decimal)
            for values in by_group.values():
                for currency, value in values.items():
                    reconciled[currency] += value
            passed = dict(reconciled) == dict(totals) and present + missing == len(rows)
            checks.append(_check(f'column_{index}_reconciliation', 'passed' if passed else 'failed',
                                 'Group totals and present/missing counts compared with all records.'))
            count_totals = {'positive': 0, 'zero': 0, 'negative': 0, 'missing': 0}
            counts_match = True
            for group, counts in counts_by_group.items():
                if sum(counts.values()) != group_counts[group]:
                    counts_match = False
                for category, count in counts.items():
                    count_totals[category] += count
            expected_counts = {'positive': positive, 'zero': zero, 'negative': negative, 'missing': missing}
            counts_match = counts_match and count_totals == expected_counts and positive + zero + negative == present
            checks.append(_check(f'column_{index}_sign_counts', 'passed' if counts_match else 'failed',
                                 'Positive, zero, negative, and missing counts reconcile by group and across all records.'))
            if missing:
                checks.append(_check(f'column_{index}_missing', 'unresolved',
                                     f'{missing} blank values excluded from totals; blanks are not known zeros.'))
            if currency_index is None:
                checks.append(_check(f'column_{index}_unit', 'unresolved',
                                     'No currency column selected. Totals do not establish a shared unit.'))
            for expected in options['expected_totals']:
                if expected['column'] != index:
                    continue
                actual = totals.get(expected['currency'])
                expected_value = parse_decimal(expected['total'], '.')
                status = 'passed' if actual is not None and actual == expected_value else 'failed'
                checks.append(_check(f'column_{index}_expected_{expected["currency"]}', status,
                                     f'Expected {expected["total"]}; observed {actual if actual is not None else "not recorded"}.'))
            measures.append({
                'column': index, 'header': header[index], 'currency_column': currency_index,
                'present': present, 'missing': missing, 'negative_records': negative,
                'positive_records': positive, 'zero_records': zero,
                'counts_by_group': counts_by_group,
                'negative_dates': len(negative_dates) if options['date_column'] is not None else None,
                'totals': _strings(totals),
                'by_group': {key: _strings(value) for key, value in sorted(by_group.items())},
                'by_month': {key: _strings(value) for key, value in sorted(by_month.items())},
            })
    if not rows:
        checks.append(_check('empty_input', 'unresolved', 'The input contains headers but no records.'))
    if not options['expected_totals']:
        checks.append(_check('independent_totals', 'unresolved', 'No independent expected totals supplied. Internal reconciliation is not external verification.'))
    statuses = {check['status'] for check in checks}
    overall = 'passed'
    if 'unresolved' in statuses:
        overall = 'unresolved'
    if 'failed' in statuses:
        overall = 'failed'
    covered_dates = [value for value in dates if value is not None]
    result = {
        'version': 2, 'source_sha256': hashlib.sha256(raw).hexdigest(), 'options': options,
        'encoding': encoding, 'headers': header, 'record_count': len(rows),
        'date_min': min(covered_dates) if covered_dates else None,
        'date_max': max(covered_dates) if covered_dates else None,
        'group_counts': dict(sorted(group_counts.items())), 'measures': measures,
        'verification': {'status': overall, 'checks': checks},
        'interpretation': 'Unresolved: units, transaction semantics, duplicate events, returns, FX rates, and causes are not inferred.',
    }
    if len(json.dumps(result, ensure_ascii=False)) > MAX_SUMMARY_CHARS:
        raise ValueError('Summary exceeds 200,000 characters; select fewer measures or groups.')
    return result


def markdown_cell(value):
    text = escape(str(value))
    for marker in ('\\', '`', '*', '_', '[', ']', '|'):
        text = text.replace(marker, '\\' + marker)
    return ' '.join(text.splitlines())


def build_report(summary: dict) -> str:
    verification = summary['verification']
    date_column = summary['options']['date_column']
    date_basis = 'Not selected; no monthly grouping is performed.'
    if date_column is not None:
        date_header = markdown_cell(summary['headers'][date_column])
        date_basis = f'{date_header} (column {date_column}, zero-based). Used for the date range, monthly totals, and negative-date counts.'
    lines = ['# CSV analysis report', '',
             f"**Verification: {verification['status'].upper()}**", '',
             '## Overview', '',
             f"Records: {summary['record_count']}. Measures: {len(summary['measures'])}. Groups: {len(summary['group_counts'])}.", '',
             f'Date basis: {date_basis}', '',
             f"Date range: {summary['date_min'] or 'not selected'} to {summary['date_max'] or 'not selected'}.", '',
             '| Measure | Recorded | Missing |', '| --- | ---: | ---: |']
    for measure in summary['measures']:
        label = f"{measure['column']}: {markdown_cell(measure['header'])}"
        lines.append(f"| {label} | {measure['present']} | {measure['missing']} |")
    lines.extend(['', '| Measure | Currency/unit | As-listed total |', '| --- | --- | ---: |'])
    for measure in summary['measures']:
        label = f"{measure['column']}: {markdown_cell(measure['header'])}"
        for currency, total in measure['totals'].items():
            lines.append(f'| {label} | {currency} | {total} |')
    lines.extend(['', 'Totals retain the recorded signs and keep currencies separate. Blank values are excluded, not treated as zero. '
                  'Populated values include explicit zeros; they do not identify a transaction type or prove profitability.', '',
             f"Source SHA-256: `{summary['source_sha256']}`.", '',
             'All tables are generated from the checked summary. Original records and signs are retained.', '',
             '## Verification', '', '| Check | Status | Detail |', '| --- | --- | --- |'])
    for check in verification['checks']:
        cells = [markdown_cell(check[key]) for key in ('name', 'status', 'detail')]
        lines.append('| ' + ' | '.join(cells) + ' |')
    lines.extend(['', '## Record counts', '', '| Group | Records |', '| --- | ---: |'])
    for group, count in summary['group_counts'].items():
        lines.append(f'| {markdown_cell(group)} | {count} |')
    for measure in summary['measures']:
        index = measure['column']
        lines.extend(['', f"## Column {index}: {markdown_cell(measure['header'])}", '',
                      f"Recorded: {measure['present']}. Missing: {measure['missing']}. Negative records: {measure['negative_records']}.", '',
                      f"Positive records: {measure['positive_records']}. Zero records: {measure['zero_records']}.", '',
                      f"Distinct dates containing negative records: {measure['negative_dates'] if measure['negative_dates'] is not None else 'date column not selected'}.", '',
                      'These are observations, not counts of distinct events or negative-balance episodes.', '',
                      '### Counts by group', '',
                      '| Group | Positive | Zero | Negative | Missing |', '| --- | ---: | ---: | ---: | ---: |'])
        for group, counts in measure['counts_by_group'].items():
            lines.append(f"| {markdown_cell(group)} | {counts['positive']} | {counts['zero']} | {counts['negative']} | {counts['missing']} |")
        lines.extend(['', '| Currency/unit | As-listed total |', '| --- | ---: |'])
        for currency, total in measure['totals'].items():
            lines.append(f'| {currency} | {total} |')
        for key, label in (('by_group', 'Group'), ('by_month', 'Month')):
            if not measure[key]:
                continue
            lines.extend(['', f'| {label} | Currency/unit | As-listed total |', '| --- | --- | ---: |'])
            for group, values in measure[key].items():
                for currency, total in values.items():
                    lines.append(f'| {markdown_cell(group)} | {currency} | {total} |')
    lines.extend(['', '## Interpretation and limitations', '', summary['interpretation'], '',
                  'Passing checks apply only to the named arithmetic checks. They do not prove that the source is correct. '
                  'Missing values are excluded and reported. No records are deduplicated, no currency conversion is performed, '
                  'and no financial return or cash-flow meaning is assigned to a sum.', '',
                  '## Method and reproduction', '',
                  f"Encoding: {summary['encoding']}. Delimiter: {summary['options']['delimiter']}. "
                  f"Decimal separator: {summary['options']['decimal_separator']}. No thousands separators are accepted.", '',
                  'The companion JSON summary records the exact column selections, numeric conventions, '
                  'checks, and source hash needed to reproduce these tables. Report generation recomputes '
                  'the results from the CSV and rejects changed inputs or edited summaries.', ''])
    report = '\n'.join(lines)
    if len(report) > MAX_SUMMARY_CHARS:
        raise ValueError('Report exceeds 200,000 characters; select fewer measures or groups.')
    return report
