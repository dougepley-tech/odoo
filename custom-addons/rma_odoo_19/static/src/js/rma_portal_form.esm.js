/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";

publicWidget.registry.RmaPortalForm = publicWidget.Widget.extend({
    selector: '.o_rma_portal_form',
    events: {
        'change select[name="operation_id"]': '_onChangeOperation',
        'change input[name="quantity"]': '_onChangeQuantity',
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
        const max = parseFloat(input.dataset.maxQty || '0');
        const val = parseFloat(input.value || '0');
        if (val > max) {
            input.value = max;
        }
        if (val < 0) {
            input.value = 0;
        }
    },
});

export default publicWidget.registry.RmaPortalForm;
