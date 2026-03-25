/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { FileViewer } from "@web/core/file_viewer/file_viewer";

patch(FileViewer.prototype, {
    /**
     * Open system print dialog for current file (PDF, images, etc.)
     */
    onClickPrintHeader() {
        const file = this.state.file;
        const viewUrl = file?.defaultSource || file?.downloadUrl;
        if (!viewUrl) return;
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
});
