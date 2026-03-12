/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { AttachmentList } from "@mail/core/common/attachment_list";
import { url } from "@web/core/utils/urls";

patch(AttachmentList.prototype, {
    /**
     * @param {import("models").Attachment} attachment
     */
    onClickPrint(attachment) {
        if (attachment.uploading) {
            return;
        }
        // Open attachment in new tab for printing (without download param so browser displays PDF)
        let viewUrl;
        if (attachment.urlRoute) {
            const params = { ...(attachment.urlQueryParams || {}) };
            delete params.download;
            viewUrl = url(attachment.urlRoute, params);
        } else if (attachment.downloadUrl) {
            viewUrl = attachment.downloadUrl.replace(/[?&]download=[^&]*/g, "").replace(/\?&/, "?");
        } else {
            return;
        }
        window.open(viewUrl, "_blank");
    },

    /**
     * @param {import("models").Attachment} attachment
     */
    canPrint(attachment) {
        return !attachment.uploading && !this.env.inComposer;
    },
});
