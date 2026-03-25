# -*- coding: utf-8 -*-
import os
import logging
from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)

# Path to the pre-built React app HTML (single self-contained file)
_APP_HTML = os.path.join(os.path.dirname(__file__), '..', 'static', 'app', 'index.html')


class IagDirectPrintController(http.Controller):

    # ── Web App ───────────────────────────────────────────────────────────────

    @http.route('/iag/print/app', type='http', auth='user', methods=['GET'], csrf=False)
    def serve_app(self):
        """
        Serve the React print client at /iag/print/app
        Uses the existing Odoo session cookie — same origin, zero CORS issues.
        """
        try:
            with open(_APP_HTML, 'r', encoding='utf-8') as f:
                html = f.read()
            return request.make_response(html, headers=[
                ('Content-Type', 'text/html; charset=utf-8'),
                ('X-Frame-Options', 'SAMEORIGIN'),
                ('Cache-Control', 'no-cache, no-store, must-revalidate'),
            ])
        except FileNotFoundError:
            return request.make_response(
                '<h1>Direct Print app not built</h1>'
                '<p>static/app/index.html not found. Re-install the module.</p>',
                headers=[('Content-Type', 'text/html')]
            )

    # ── Printers ──────────────────────────────────────────────────────────────

    @http.route('/iag/print/printers', type='json', auth='user', methods=['POST'])
    def list_printers(self):
        printers = request.env['iag.printer'].search([('active', '=', True)])
        return [self._printer_dict(p) for p in printers]

    @http.route('/iag/print/printers/check_status', type='json', auth='user', methods=['POST'])
    def check_status(self, printer_ids=None):
        domain = [('active', '=', True)]
        if printer_ids:
            domain.append(('id', 'in', printer_ids))
        printers = request.env['iag.printer'].search(domain)
        for p in printers:
            p._check_status()
        return [self._printer_dict(p) for p in printers]

    @http.route('/iag/print/printers/test', type='json', auth='user', methods=['POST'])
    def test_printer(self, printer_id):
        printer = request.env['iag.printer'].browse(int(printer_id))
        if not printer.exists():
            return {'error': f'Printer {printer_id} not found'}
        try:
            printer.action_test_print()
            return {'success': True, 'message': f'Test job sent to {printer.name}'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    @http.route('/iag/print/printers/create', type='json', auth='user', methods=['POST'])
    def create_printer(self, vals):
        printer = request.env['iag.printer'].create(vals)
        return self._printer_dict(printer)

    @http.route('/iag/print/printers/write', type='json', auth='user', methods=['POST'])
    def write_printer(self, printer_id, vals):
        printer = request.env['iag.printer'].browse(int(printer_id))
        if not printer.exists():
            return {'error': 'Not found'}
        printer.write(vals)
        return self._printer_dict(printer)

    @http.route('/iag/print/printers/delete', type='json', auth='user', methods=['POST'])
    def delete_printer(self, printer_id):
        printer = request.env['iag.printer'].browse(int(printer_id))
        if printer.exists():
            printer.active = False
        return {'success': True}

    # ── Reports ───────────────────────────────────────────────────────────────

    @http.route('/iag/print/reports', type='json', auth='user', methods=['POST'])
    def list_reports(self):
        reports = request.env['ir.actions.report'].search([
            ('report_type', 'in', ['qweb-pdf', 'qweb-text']),
        ], order='name')
        IrModel = request.env['ir.model']
        result = []
        for r in reports:
            ext_id = r.get_external_id().get(r.id) or ''
            model_rec = IrModel.search([('model', '=', r.model)], limit=1)
            model_label = model_rec.name if model_rec else r.model
            # Build a clearer description: "Transfer (Stock)" or "Timesheet (HR)"
            module = r.model.split('.')[0] if r.model else ''
            module_display = module.replace('_', ' ').title() if module else ''
            if model_rec and model_rec.info:
                info = (model_rec.info or '').strip().split('\n')[0][:80]
                model_description = f"{model_label} — {info}" if info else model_label
            elif module_display and module_display.lower() != model_label.lower():
                model_description = f"{model_label} ({module_display})"
            else:
                model_description = model_label
            result.append({
                'id': r.id,
                'xml_id': ext_id,
                'name': r.name,
                'model': r.model,
                'model_label': model_label,
                'model_description': model_description,
                'report_type': r.report_type,
                'doc_type': 'zpl' if r.report_type == 'qweb-text' else 'pdf',
            })
        return result

    # ── Print job ─────────────────────────────────────────────────────────────

    @http.route('/iag/print/send', type='json', auth='user', methods=['POST'])
    def send_print_job(self, report_xml_id, res_model, res_id, printer_id, copies=1):
        try:
            record = request.env[res_model].browse(int(res_id))
            if not record.exists():
                return {'success': False, 'error': f'{res_model} #{res_id} not found'}

            if hasattr(record, 'iag_direct_print'):
                result = record.iag_direct_print(
                    report_xml_id=report_xml_id,
                    printer_id=int(printer_id),
                    copies=int(copies),
                )
            else:
                report = request.env.ref(report_xml_id)
                if report.report_type == 'qweb-pdf':
                    data, _ = request.env['ir.actions.report']._render_qweb_pdf(
                        report_xml_id, [int(res_id)])
                    doc_type = 'pdf'
                else:
                    data, _ = request.env['ir.actions.report']._render_qweb_text(
                        report_xml_id, [int(res_id)])
                    doc_type = 'zpl'
                    if isinstance(data, str):
                        data = data.encode()
                printer = request.env['iag.printer'].browse(int(printer_id))
                printer.print_document(data, doc_type=doc_type,
                                       title=f'Odoo #{res_id}', copies=int(copies))
                request.env['iag.print.job'].sudo().create({
                    'name': f'{report_xml_id.split(".")[-1]} → {printer.name}',
                    'printer_id': printer.id,
                    'report_xml_id': report_xml_id,
                    'res_model': res_model,
                    'res_id': int(res_id),
                    'state': 'done',
                    'copies': int(copies),
                    'size_bytes': len(data),
                })
                result = {'printer': printer.name, 'doc_type': doc_type, 'bytes': len(data)}

            return {'success': True, **result}

        except Exception as e:
            _logger.exception('IAG Direct Print: send_print_job failed')
            return {'success': False, 'error': str(e)}

    # ── Scenarios ─────────────────────────────────────────────────────────────

    @http.route('/iag/print/scenarios', type='json', auth='user', methods=['POST'])
    def list_scenarios(self):
        # Include inactive scenarios so users can see and reactivate them
        scenarios = request.env['iag.print.scenario'].with_context(active_test=False).search([])
        return [self._scenario_dict(s) for s in scenarios]

    @http.route('/iag/print/scenarios/create', type='json', auth='user', methods=['POST'])
    def create_scenario(self, vals):
        s = request.env['iag.print.scenario'].create(vals)
        return self._scenario_dict(s)

    @http.route('/iag/print/scenarios/toggle', type='json', auth='user', methods=['POST'])
    def toggle_scenario(self, scenario_id, active):
        s = request.env['iag.print.scenario'].browse(int(scenario_id))
        if s.exists():
            s.active = bool(active)
        return self._scenario_dict(s)

    @http.route('/iag/print/scenarios/update', type='json', auth='user', methods=['POST'])
    def update_scenario(self, scenario_id, vals):
        s = request.env['iag.print.scenario'].browse(int(scenario_id))
        if not s.exists():
            return {'error': 'Scenario not found'}
        s.write(vals)
        return self._scenario_dict(s)

    @http.route('/iag/print/scenarios/delete', type='json', auth='user', methods=['POST'])
    def delete_scenario(self, scenario_id):
        s = request.env['iag.print.scenario'].browse(int(scenario_id))
        if s.exists():
            s.unlink()
        return {'success': True}

    # ── User rules ────────────────────────────────────────────────────────────

    @http.route('/iag/print/user_rules', type='json', auth='user', methods=['POST'])
    def list_user_rules(self):
        rules = request.env['iag.print.user.rule'].search([])
        return [{
            'id': r.id,
            'user_id': r.user_id.id,
            'user_name': r.user_id.name,
            'report_id': r.report_id.id,
            'report_name': r.report_id.name,
            'printer_id': r.printer_id.id,
            'printer_name': r.printer_id.name,
            'copies': r.copies,
        } for r in rules]

    @http.route('/iag/print/user_rules/save', type='json', auth='user', methods=['POST'])
    def save_user_rule(self, vals):
        existing = request.env['iag.print.user.rule'].search([
            ('user_id', '=', vals.get('user_id')),
            ('report_id', '=', vals.get('report_id')),
        ], limit=1)
        if existing:
            existing.write(vals)
            r = existing
        else:
            r = request.env['iag.print.user.rule'].create(vals)
        return {'id': r.id, 'success': True}

    @http.route('/iag/print/user_rules/update', type='json', auth='user', methods=['POST'])
    def update_user_rule(self, rule_id, vals):
        r = request.env['iag.print.user.rule'].browse(int(rule_id))
        if not r.exists():
            return {'error': 'Rule not found'}
        r.write(vals)
        return {'id': r.id, 'success': True}

    @http.route('/iag/print/user_rules/delete', type='json', auth='user', methods=['POST'])
    def delete_user_rule(self, rule_id):
        r = request.env['iag.print.user.rule'].browse(int(rule_id))
        if r.exists():
            r.unlink()
        return {'success': True}

    # ── Print jobs log ────────────────────────────────────────────────────────

    @http.route('/iag/print/jobs', type='json', auth='user', methods=['POST'])
    def list_jobs(self, limit=100):
        jobs = request.env['iag.print.job'].search(
            [], limit=int(limit), order='create_date desc')
        return [{
            'id': j.id,
            'name': j.name,
            'printer': j.printer_id.name if j.printer_id else '—',
            'report': j.report_xml_id or '—',
            'res_name': j.res_name or '—',
            'state': j.state,
            'copies': j.copies,
            'size_bytes': j.size_bytes,
            'error': j.error_message or '',
            'time': j.create_date.isoformat() if j.create_date else '',
        } for j in jobs]

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _printer_dict(self, p):
        return {
            'id': p.id,
            'name': p.name,
            'printer_type': p.printer_type,
            'location': p.location or '',
            'host': p.host or '',
            'port': p.port,
            'cups_name': p.cups_name or '',
            'cups_host': p.cups_host or 'localhost',
            'cups_port': p.cups_port or 631,
            'paper_format': p.paper_format or '',
            'dpi': p.dpi,
            'copies': p.copies,
            'status': p.status,
            'last_check': p.last_check.isoformat() if p.last_check else '',
            'last_error': p.last_error or '',
        }

    def _scenario_dict(self, s):
        return {
            'id': s.id,
            'name': s.name,
            'active': s.active,
            'trigger_event': s.trigger_event,
            'report_id': s.report_id.id if s.report_id else None,
            'report_name': s.report_id.name if s.report_id else '',
            'printer_id': s.printer_id.id if s.printer_id else None,
            'printer_name': s.printer_id.name if s.printer_id else '',
            'copies': s.copies,
            'domain_filter': s.domain_filter or '',
            'run_count': s.run_count,
            'last_run': s.last_run.isoformat() if s.last_run else '',
        }
