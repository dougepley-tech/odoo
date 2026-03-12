/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { FileViewer } from "@web/core/file_viewer/file_viewer";

patch(FileViewer.prototype, {
    /**
     * Open current file in new tab for printing (works for PDF, images, etc.)
     */
    onClickPrintHeader() {
        const file = this.state.file;
        if (file?.defaultSource || file?.downloadUrl) {
            const url = file.defaultSource || file.downloadUrl;
            window.open(url, "_blank");
        }
    },
});
