# -*- coding: utf-8 -*-
"""
pyNBT.excel_io

Minimal, dependency-free XLSX reader/writer for pyRevit's IronPython 2.7
engine (openpyxl and friends are Python-3-only and unavailable there).

write_xlsx() writes a single-sheet workbook using inline strings (no
shared-strings table needed) - simpler to generate correctly, and Excel
opens it natively. read_xlsx() reads back the FIRST worksheet of any
.xlsx file, resolving shared strings, inline strings and numeric/formula
-result cells, aligned to the header row's column count.

Added 2026-09 for the "Excel Data" tool - replaces the older standalone
`pynbt_excel.py` module that lived outside the pyNBT package (flat file
under a capitalized `Lib/` folder in NBT's original, pre-pyNBT scripts).
"""
from __future__ import unicode_literals

import os
import re
import zipfile
from xml.etree import ElementTree as ET


_NS = {
    'main': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
    'rel': 'http://schemas.openxmlformats.org/package/2006/relationships',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}


def _tag(ns_key, name):
    return '{{{0}}}{1}'.format(_NS[ns_key], name)


def _col_letters_to_index(letters):
    """'A' -> 0, 'B' -> 1, ... 'AA' -> 26"""
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch.upper()) - ord('A') + 1)
    return index - 1


def _col_index_to_letters(index):
    result = ''
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _split_cell_ref(ref):
    match = re.match(r'^([A-Za-z]+)(\d+)$', ref or '')
    if not match:
        return 0, 0
    letters, digits = match.groups()
    return _col_letters_to_index(letters), int(digits) - 1


def _first_sheet_part(zf):
    """Resolve the workbook's first sheet to its XML part path inside the zip."""
    workbook_xml = ET.fromstring(zf.read('xl/workbook.xml'))
    sheets = workbook_xml.find(_tag('main', 'sheets'))
    first_sheet = sheets.find(_tag('main', 'sheet')) if sheets is not None else None
    rid = first_sheet.get(_tag('r', 'id')) if first_sheet is not None else None

    if rid and 'xl/_rels/workbook.xml.rels' in zf.namelist():
        rels_xml = ET.fromstring(zf.read('xl/_rels/workbook.xml.rels'))
        for rel in rels_xml.findall(_tag('rel', 'Relationship')):
            if rel.get('Id') == rid:
                target = (rel.get('Target') or '').replace('\\', '/')
                if target.startswith('/'):
                    return target.lstrip('/')
                return 'xl/' + target

    return 'xl/worksheets/sheet1.xml'


def _load_shared_strings(zf):
    if 'xl/sharedStrings.xml' not in zf.namelist():
        return []
    root = ET.fromstring(zf.read('xl/sharedStrings.xml'))
    values = []
    for si in root.findall(_tag('main', 'si')):
        texts = [node.text or '' for node in si.iter(_tag('main', 't'))]
        values.append(''.join(texts))
    return values


def _cell_text(cell, shared_strings):
    cell_type = cell.get('t')

    if cell_type == 's':
        value_node = cell.find(_tag('main', 'v'))
        if value_node is None or not value_node.text:
            return ''
        try:
            return shared_strings[int(value_node.text)]
        except (ValueError, IndexError):
            return ''

    if cell_type == 'inlineStr':
        is_node = cell.find(_tag('main', 'is'))
        if is_node is None:
            return ''
        return ''.join(node.text or '' for node in is_node.iter(_tag('main', 't')))

    if cell_type == 'str':
        value_node = cell.find(_tag('main', 'v'))
        return value_node.text if value_node is not None and value_node.text else ''

    # plain numeric (no "t" attribute) or boolean
    value_node = cell.find(_tag('main', 'v'))
    if value_node is None or value_node.text is None:
        return ''
    text = value_node.text
    if cell_type == 'b':
        return 'TRUE' if text == '1' else 'FALSE'
    try:
        as_float = float(text)
        if as_float == int(as_float):
            return str(int(as_float))
        return str(as_float)
    except ValueError:
        return text


def read_xlsx(path):
    """Read the first worksheet of an .xlsx file.

    Returns (headers, rows): headers is a list of strings from row 1, rows
    is a list of lists of strings, each padded/truncated to len(headers).
    """
    with zipfile.ZipFile(path, 'r') as zf:
        sheet_part = _first_sheet_part(zf)
        shared_strings = _load_shared_strings(zf)
        sheet_xml = ET.fromstring(zf.read(sheet_part))

        sheet_data = sheet_xml.find(_tag('main', 'sheetData'))
        raw_rows = []
        if sheet_data is not None:
            for row_node in sheet_data.findall(_tag('main', 'row')):
                row_values = {}
                for cell in row_node.findall(_tag('main', 'c')):
                    col_index, _ = _split_cell_ref(cell.get('r'))
                    row_values[col_index] = _cell_text(cell, shared_strings)
                raw_rows.append(row_values)

    if not raw_rows:
        return [], []

    header_map = raw_rows[0]
    col_count = (max(header_map.keys()) + 1) if header_map else 0
    headers = [header_map.get(i, '').strip() for i in range(col_count)]

    rows = []
    for row_values in raw_rows[1:]:
        if not row_values:
            continue
        rows.append([row_values.get(i, '') for i in range(col_count)])

    return headers, rows


def _xml_escape(value):
    text = '' if value is None else str(value)
    return (
        text.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )


def write_xlsx(path, headers, rows, sheet_name='Sheet1'):
    """Write a single-sheet .xlsx using inline strings (no shared-strings
    table). Every value is written as text, matching how this tool reads
    values back in (every parameter round-trips as a string)."""

    safe_sheet_name = _xml_escape(sheet_name)[:31] or 'Sheet1'

    def cell_xml(col_index, row_index, text):
        ref = '{0}{1}'.format(_col_index_to_letters(col_index), row_index)
        if not text:
            return '<c r="{0}" t="inlineStr"><is><t></t></is></c>'.format(ref)
        return '<c r="{0}" t="inlineStr"><is><t xml:space="preserve">{1}</t></is></c>'.format(
            ref, _xml_escape(text)
        )

    all_rows = [headers] + list(rows)
    row_xml_parts = []
    for row_index, row in enumerate(all_rows, start=1):
        cells = ''.join(cell_xml(col_index, row_index, value) for col_index, value in enumerate(row))
        row_xml_parts.append('<row r="{0}">{1}</row>'.format(row_index, cells))

    dimension_ref = 'A1:{0}{1}'.format(
        _col_index_to_letters(max(len(headers) - 1, 0)), max(len(all_rows), 1)
    )

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<dimension ref="{dimension}"/>'
        '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        '<sheetData>{rows}</sheetData>'
        '</worksheet>'
    ).format(dimension=dimension_ref, rows=''.join(row_xml_parts))

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        '</Relationships>'
    )

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    ).format(name=safe_sheet_name)

    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )

    if os.path.exists(path):
        os.remove(path)

    # IronPython 2.7's zipfile.writestr() encodes a `unicode` argument using
    # the ASCII codec by default instead of UTF-8 - encode explicitly to
    # bytes here or any non-ASCII text (Vietnamese diacritics in a schedule
    # name, a Comments value, etc.) throws UnicodeEncodeError on write.
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml', content_types.encode('utf-8'))
        zf.writestr('_rels/.rels', root_rels.encode('utf-8'))
        zf.writestr('xl/workbook.xml', workbook_xml.encode('utf-8'))
        zf.writestr('xl/_rels/workbook.xml.rels', workbook_rels.encode('utf-8'))
        zf.writestr('xl/worksheets/sheet1.xml', sheet_xml.encode('utf-8'))
