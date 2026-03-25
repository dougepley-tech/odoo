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
        // Get view URL (without download param so browser displays PDF)
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
        // Open in new window and trigger print when loaded (keeps dialog open)
        const printWin = window.open(viewUrl, "_blank", "width=800,height=600");
        if (printWin) {
            printWin.onload = () => {
                setTimeout(() => {
                    try {
                        printWin.print();
                    } catch {
                        // Print failed; user can still Ctrl+P in the window
                    }
                }, 500);
            };
        }
    },

    /**
     * @param {import("models").Attachment} attachment
     */
    canPrint(attachment) {
        return !attachment.uploading && !this.env.inComposer;
    },
});
