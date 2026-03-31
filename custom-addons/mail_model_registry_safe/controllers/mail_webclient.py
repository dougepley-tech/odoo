# -*- coding: utf-8 -*-
"""Patch mail Discuss init so /mail/data does not crash on orphan model names."""

from collections import defaultdict

from odoo.http import request
from odoo.addons.mail.controllers.webclient import WebclientController as MailWebclientController
from odoo.addons.mail.tools.discuss import Store


class WebclientController(MailWebclientController):
    @classmethod
    def _process_request_for_logged_in_user(self, store: Store, name, params):
        if name != "failures":
            return super()._process_request_for_logged_in_user(store, name, params)
        domain = [
            ("author_id", "=", request.env.user.partner_id.id),
            ("notification_status", "in", ("bounce", "exception")),
            ("mail_message_id.message_type", "!=", "user_notification"),
            ("mail_message_id.model", "!=", False),
            ("mail_message_id.res_id", "!=", 0),
        ]
        notifications = request.env["mail.notification"].sudo().search(domain, limit=100)
        found = defaultdict(list)
        models = request.env.registry.models
        for message in notifications.mail_message_id:
            if message.model not in models:
                continue
            found[message.model].append(message.res_id)
        existing = {
            model: set(request.env[model].browse(ids).exists().ids)
            for model, ids in found.items()
        }
        valid = notifications.filtered(
            lambda n: n.mail_message_id.model in existing
            and n.mail_message_id.res_id in existing[n.mail_message_id.model]
        )
        lost = notifications - valid
        if lost:
            lost.sudo().unlink()
        valid.mail_message_id._message_notifications_to_store(store)
