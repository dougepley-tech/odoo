/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";

publicWidget.registry.RmaPortalForm = publicWidget.Widget.extend({
    selector: '.o_rma_portal_form',
    events: {
        'change select[name="operation_id"]': '_onChangeOperation',
        'change input.o_rma_qty_input': '_onChangeQuantity',
        'input input.o_rma_qty_input': '_onChangeQuantity',
    },

    _onChangeOperation(ev) {
        const val = ev.currentTarget.value;
        const reasonField = this.el.querySelector('.o_rma_reason_field');
        if (reasonField) {
            reasonField.style.display = val ? '' : 'none';
        }
    },

    _onChangeQuantity(ev) {
        const input = ev.currentTarget;
        const max = Math.floor(parseFloat(input.dataset.maxQty || '0'));
        let val = Math.floor(parseFloat(input.value || '0'));
        if (Number.isNaN(val)) {
            val = 0;
        }
        if (val > max) {
            val = max;
        }
        if (val < 0) {
            val = 0;
        }
        input.value = String(val);
    },
});

export default publicWidget.registry.RmaPortalForm;
