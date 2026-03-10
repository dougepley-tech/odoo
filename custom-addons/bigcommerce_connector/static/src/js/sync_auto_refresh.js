/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";

patch(FormController.prototype, {
    setup() {
        super.setup();
        this.orm = useService("orm");
        this.autoRefreshInterval = null;
        this._checkAutoRefresh();
    },

    onWillUnmount() {
        super.onWillUnmount();
        this._stopAutoRefresh();
    },

    async onWillStart() {
        await super.onWillStart();
        // Also check after willStart in case model wasn't ready
        this._checkAutoRefresh();
    },

    async onWillUpdateProps() {
        await super.onWillUpdateProps();
        // Re-check auto-refresh when props update (e.g., when navigating back to the view)
        this._checkAutoRefresh();
    },

    _checkAutoRefresh() {
        // Only auto-refresh if this is a sync model
        const model = this.props.resModel;
        const isSyncModel = model && (
            model === 'bigcommerce.product.sync' ||
            model === 'bigcommerce.order.sync' ||
            model === 'bigcommerce.inventory.sync' ||
            model === 'bigcommerce.customer.sync' ||
            model === 'bigcommerce.fulfillment.sync' ||
            model === 'bigcommerce.sync.operation'
        );

        if (isSyncModel) {
            // Stop any existing auto-refresh first (in case we're re-initializing)
            this._stopAutoRefresh();
            // Start immediately and also check after a short delay
            this._startAutoRefresh();
            // Also refresh immediately when button is clicked (handled by watching state)
            this._watchState();
            console.log(`[BigCommerce Auto-Refresh] Started auto-refresh for model: ${model}`);
        } else {
            // Not a sync model - stop auto-refresh if it's running
            this._stopAutoRefresh();
        }
    },

    _watchState() {
        // Watch for state changes to refresh immediately when sync starts
        const record = this.model.root;
        if (record && record.data) {
            // Monitor state field changes
            const stateField = record.data.state;
            if (stateField) {
                // If state changes to running, refresh immediately
                const checkState = () => {
                    const currentState = record.data ? record.data.state : null;
                    if (currentState === 'running') {
                        // State is running, ensure auto-refresh is active
                        if (!this.autoRefreshInterval) {
                            this._startAutoRefresh();
                        }
                    }
                };
                
                // Check state periodically (but less frequently since we have auto-refresh)
                const stateCheckInterval = setInterval(() => {
                    checkState();
                }, 2000); // Check every 2 seconds
                
                // Store interval to clean up
                this.stateCheckInterval = stateCheckInterval;
            }
        }
    },

    async _refreshRecord() {
        try {
            const record = this.model.root;
            if (!record || !record.resId) {
                return;
            }
            
            const model = this.props.resModel;
            if (!model) {
                return;
            }
            
            // Determine which fields to read based on the model
            // Start with common fields that exist on all sync models
            let fieldsToRead = ['state'];
            
            // Add model-specific fields based on what actually exists
            if (model === 'bigcommerce.product.sync') {
                fieldsToRead = fieldsToRead.concat([
                    'processed_items', 
                    'total_items', 
                    'current_item',
                    'progress_percentage',
                    'products_created',
                    'products_updated',
                    'products_failed',
                    'products_skipped',
                ]);
            } else if (model === 'bigcommerce.order.sync') {
                fieldsToRead = fieldsToRead.concat([
                    'processed_items', 
                    'total_items', 
                    'current_item',
                    'progress_percentage',
                    'orders_created',
                    'orders_updated',
                    'orders_failed',
                ]);
            } else if (model === 'bigcommerce.sync.operation') {
                fieldsToRead = fieldsToRead.concat([
                    'processed_items', 
                    'total_items', 
                    'current_item',
                    'progress_percentage',
                    'items_synced',
                    'items_created',
                    'items_updated',
                    'items_failed',
                ]);
            } else {
                // For other sync models, try common progress fields
                fieldsToRead = fieldsToRead.concat([
                    'processed_items', 
                    'total_items', 
                    'current_item',
                    'progress_percentage',
                ]);
            }
            
            // Read fresh data from server with error handling
            let data = null;
            try {
                // Check again before making RPC call
                if (this._isDestroyed) {
                    return;
                }
                
                data = await this.orm.read(model, [record.resId], fieldsToRead);
            } catch (readError) {
                // Check if error is due to component being destroyed
                const errorMessage = readError?.message || String(readError || '');
                if (this._isDestroyed || errorMessage.includes('destroyed') || errorMessage.includes('Component is destroyed')) {
                    return;
                }
                
                // If read fails, try with just the state field
                console.warn(`[BigCommerce Auto-Refresh] Failed to read all fields, trying state only:`, readError);
                try {
                    // Check again before second RPC call
                    if (this._isDestroyed) {
                        return;
                    }
                    
                    data = await this.orm.read(model, [record.resId], ['state']);
                } catch (stateError) {
                    // Check if error is due to component being destroyed
                    const stateErrorMessage = stateError?.message || String(stateError || '');
                    if (this._isDestroyed || stateErrorMessage.includes('destroyed') || stateErrorMessage.includes('Component is destroyed')) {
                        return;
                    }
                    // If even state read fails, the record might not exist or we don't have permission
                    console.error(`[BigCommerce Auto-Refresh] Failed to read record state:`, stateError);
                    return;
                }
            }
            
            if (data && data.length > 0) {
                const freshData = data[0];
                
                // Invalidate ALL fields of the record to force fresh read
                if (record.invalidate) {
                    record.invalidate();
                }
                
                // Update record data with fresh values
                if (record.data) {
                    // Only update fields that were successfully read
                    for (const [key, value] of Object.entries(freshData)) {
                        if (key in freshData) {
                            record.data[key] = value;
                        }
                    }
                }
                
                // Force a complete reload of the record
                // This will trigger recomputation of computed fields and update the UI
                try {
                    if (record.load) {
                        await record.load();
                    }
                } catch (loadError) {
                    console.warn(`[BigCommerce Auto-Refresh] Failed to reload record:`, loadError);
                }
                
                // Force a re-render to update the UI
                // Use requestAnimationFrame to ensure DOM updates happen smoothly
                requestAnimationFrame(() => {
                    if (this.render) {
                        this.render();
                    }
                });
            }
        } catch (error) {
            // Log error but don't throw - we want auto-refresh to continue
            console.error('[BigCommerce Auto-Refresh] Error refreshing record:', error);
        }
    },

    _startAutoRefresh() {
        // Don't start if already running
        if (this.autoRefreshInterval) {
            return;
        }

        // Check if state is running and refresh periodically
        const checkAndRefresh = async () => {
            try {
                const record = this.model.root;
                if (!record || !record.resId) {
                    return;
                }

                // Always check state from server first to get the most current state
                // Check if component is still mounted before making RPC call
                if (this._isDestroyed) {
                    this._stopAutoRefresh();
                    return;
                }
                
                const model = this.props.resModel;
                let serverState = null;
                let stateData = null;
                
                try {
                    stateData = await this.orm.read(model, [record.resId], ['state']);
                    if (stateData && stateData.length > 0) {
                        serverState = stateData[0].state;
                    }
                } catch (stateError) {
                    // Check if error is due to component being destroyed
                    const errorMessage = stateError?.message || String(stateError || '');
                    if (this._isDestroyed || errorMessage.includes('destroyed') || errorMessage.includes('Component is destroyed')) {
                        this._stopAutoRefresh();
                        return;
                    }
                    // If state read fails for other reasons, fall back to local state
                    console.warn(`[BigCommerce Auto-Refresh] Failed to read state from server:`, stateError);
                }
                
                const currentState = record.data ? record.data.state : null;
                
                if (serverState === 'running') {
                    // State is running - always refresh to get latest progress
                    await this._refreshRecord();
                } else if (serverState !== null && serverState !== currentState) {
                    // State changed (stopped running), refresh immediately to show final state
                    await this._refreshRecord();
                    // Stop refreshing if sync is done or errored
                    if (serverState !== 'running') {
                        this._stopAutoRefresh();
                        console.log(`[BigCommerce Auto-Refresh] Stopped auto-refresh - state changed to: ${serverState}`);
                    }
                } else if (serverState === null && currentState === 'running') {
                    // Fallback: if we couldn't read state from server but local state is running, refresh anyway
                    await this._refreshRecord();
                }
            } catch (error) {
                console.error('[BigCommerce Auto-Refresh] Error during auto-refresh:', error);
                // Don't stop on error, just log it
            }
        };

        // Start auto-refresh every 1 second for real-time updates
        this.autoRefreshInterval = setInterval(checkAndRefresh, 1000);
        
        // Also do an immediate check
        checkAndRefresh();
    },

    _stopAutoRefresh() {
        if (this.autoRefreshInterval) {
            clearInterval(this.autoRefreshInterval);
            this.autoRefreshInterval = null;
        }
        if (this.stateCheckInterval) {
            clearInterval(this.stateCheckInterval);
            this.stateCheckInterval = null;
        }
        if (this.quickCheckInterval) {
            clearInterval(this.quickCheckInterval);
            this.quickCheckInterval = null;
        }
    },
});

