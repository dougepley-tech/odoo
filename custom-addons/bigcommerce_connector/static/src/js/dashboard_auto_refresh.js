/** @odoo-module **/

import { KanbanController } from "@web/views/kanban/kanban_controller";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";

patch(KanbanController.prototype, {
    setup() {
        super.setup();
        this.orm = useService("orm");
        this.autoRefreshInterval = null;
        this._isDestroyed = false;
        this._checkAutoRefresh();
    },

    onWillUnmount() {
        super.onWillUnmount();
        this._isDestroyed = true;
        this._stopAutoRefresh();
    },

    async onWillStart() {
        await super.onWillStart();
        // Also check after willStart in case model wasn't ready
        this._checkAutoRefresh();
    },

    async onWillUpdateProps() {
        await super.onWillUpdateProps();
        // Re-setup cancel buttons when props update
        const model = this.props.resModel;
        if (model && model === 'bigcommerce.dashboard') {
            setTimeout(() => this._setupCancelButtons(), 500);
        }
    },

    _checkAutoRefresh() {
        // Only auto-refresh if this is the dashboard model
        const model = this.props.resModel;
        const isDashboard = model && model === 'bigcommerce.dashboard';

        if (isDashboard) {
            // Stop any existing auto-refresh first (in case we're re-initializing)
            this._stopAutoRefresh();
            // Start auto-refresh for dashboard
            this._startAutoRefresh();
            // Setup cancel button handlers (after a delay to ensure DOM is ready)
            setTimeout(() => this._setupCancelButtons(), 500);
            console.log('[BigCommerce Dashboard] Started auto-refresh for dashboard');
        } else {
            // Not dashboard - stop auto-refresh if it's running
            this._stopAutoRefresh();
        }
    },

    _setupCancelButtons() {
        // Setup event handlers for cancel buttons
        // Use event delegation since buttons are in dynamically generated HTML
        const container = this.el;
        if (container) {
            container.addEventListener('click', async (event) => {
                const cancelBtn = event.target.closest('.cancel-sync-btn');
                if (cancelBtn) {
                    event.preventDefault();
                    const syncOpId = parseInt(cancelBtn.getAttribute('data-sync-op-id'));
                    if (syncOpId && confirm('Are you sure you want to cancel this sync operation?')) {
                        try {
                            await this.orm.call('bigcommerce.sync.operation', 'action_cancel', [[syncOpId]]);
                            // Refresh the dashboard to show updated state
                            await this._refreshDashboard();
                            this.render();
                        } catch (error) {
                            console.error('Error cancelling sync operation:', error);
                            alert('Failed to cancel sync operation: ' + (error.message || 'Unknown error'));
                        }
                    }
                }
            });
        }
    },

    async _refreshDashboard() {
        try {
            // Check if component is still mounted before proceeding
            if (this._isDestroyed || !this.model || !this.model.records) {
                return;
            }
            
            // Reload all records in the kanban view to get latest data
            if (this.model && this.model.records) {
                // Get all record IDs
                const recordIds = this.model.records
                    .filter(r => r && r.resId)
                    .map(r => r.resId);
                
                if (recordIds.length > 0) {
                    // Invalidate all records first to force fresh data
                    // This will force recomputation of computed fields when records are reloaded
                    for (const record of this.model.records) {
                        if (record && record.invalidate) {
                            // Invalidate all fields including computed fields
                            record.invalidate();
                        }
                    }
                    
                    // Check again if component is still mounted before making RPC calls
                    if (this._isDestroyed || !this.model || !this.model.records) {
                        return;
                    }
                    
                    // Read fresh data from server - this will trigger recomputation of computed fields
                    // Read the fields that include running syncs
                    let dashboardData = null;
                    try {
                        dashboardData = await this.orm.read(
                            'bigcommerce.dashboard',
                            recordIds,
                            [
                                'config_id',
                                'has_running_syncs',
                                'running_sync_count',
                                'running_sync_display',
                                'running_sync_ids',
                            ]
                        );
                    } catch (readError) {
                        // Check if error is due to component being destroyed
                        if (this._isDestroyed || (readError.message && readError.message.includes('destroyed'))) {
                            return;
                        }
                        console.error('[BigCommerce Dashboard] Failed to read dashboard data:', readError);
                        return;
                    }
                    
                    // Update record data with fresh dashboard data including computed fields
                    // This ensures the UI shows the latest running_sync_display
                    if (dashboardData && dashboardData.length > 0 && this.model && this.model.records) {
                        for (let i = 0; i < dashboardData.length && i < this.model.records.length; i++) {
                            const freshData = dashboardData[i];
                            const record = this.model.records[i];
                            if (record && record.data && freshData) {
                                // Update all fields including computed fields
                                Object.assign(record.data, freshData);
                            }
                        }
                    }
                    
                    if (!dashboardData || dashboardData.length === 0) {
                        return;
                    }
                    
                    // Check again before processing sync operations
                    if (this._isDestroyed || !this.model || !this.model.records) {
                        return;
                    }
                    
                    // Also refresh the sync operation records themselves to get latest progress
                    // This ensures the progress shown in running_sync_display is up-to-date
                    for (const dashboard of dashboardData) {
                        if (dashboard.running_sync_ids && dashboard.running_sync_ids.length > 0) {
                            try {
                                // Read fresh data from sync operations to get latest progress
                                await this.orm.read(
                                    'bigcommerce.sync.operation',
                                    dashboard.running_sync_ids,
                                    [
                                        'state',
                                        'processed_items',
                                        'total_items',
                                        'current_item',
                                        'items_synced',
                                        'items_created',
                                        'items_updated',
                                        'items_failed',
                                        'progress_percentage',
                                    ]
                                );
                                
                                // Invalidate the sync operation records in the model cache
                                // This forces the dashboard's computed field to recompute with fresh data
                                const syncOpModel = this.env.models && this.env.models['bigcommerce.sync.operation'];
                                if (syncOpModel) {
                                    for (const syncOpId of dashboard.running_sync_ids) {
                                        const syncOpRecord = syncOpModel.records.find(r => r.resId === syncOpId);
                                        if (syncOpRecord && syncOpRecord.invalidate) {
                                            syncOpRecord.invalidate();
                                        }
                                    }
                                }
                            } catch (syncOpError) {
                                // Check if error is due to component being destroyed
                                const errorMessage = syncOpError?.message || String(syncOpError || '');
                                if (this._isDestroyed || errorMessage.includes('destroyed') || errorMessage.includes('Component is destroyed')) {
                                    return;
                                }
                                console.warn('[BigCommerce Dashboard] Failed to read sync operation data:', syncOpError);
                                // Continue with other dashboards even if one fails
                            }
                        }
                    }
                    
                    // Check again before forcing recomputation
                    if (this._isDestroyed || !this.model || !this.model.records) {
                        return;
                    }
                    
                    // Force recomputation of dashboard computed fields by calling the recompute method
                    // This ensures running_sync_display is updated with latest progress
                    for (const dashboard of dashboardData) {
                        // Check if component is still mounted before each iteration
                        if (this._isDestroyed || !this.model || !this.model.records) {
                            return;
                        }
                        
                        if (dashboard.id) {
                            // Always call recompute, even if there are no running syncs
                            // This ensures the display is updated when syncs complete
                            try {
                                await this.orm.call('bigcommerce.dashboard', 'action_recompute_running_syncs', [[dashboard.id]]);
                                
                                // Check again before reading updated data
                                if (this._isDestroyed || !this.model || !this.model.records) {
                                    return;
                                }
                                
                                // Read the updated computed field after recomputation
                                // This ensures we get the latest running_sync_display
                                try {
                                    const updatedData = await this.orm.read(
                                        'bigcommerce.dashboard',
                                        [dashboard.id],
                                        ['running_sync_display', 'has_running_syncs', 'running_sync_count', 'running_sync_ids']
                                    );
                                    
                                    // Check again before updating record data
                                    if (this._isDestroyed || !this.model || !this.model.records) {
                                        return;
                                    }
                                    
                                    if (updatedData && updatedData.length > 0) {
                                        // Find the corresponding record and update it
                                        const record = this.model.records.find(r => r.resId === dashboard.id);
                                        if (record && record.data) {
                                            // Update all computed fields with fresh data
                                            Object.assign(record.data, updatedData[0]);
                                            // Invalidate to ensure UI updates
                                            if (record.invalidate) {
                                                record.invalidate(['running_sync_display', 'has_running_syncs', 'running_sync_count', 'running_sync_ids']);
                                            }
                                        }
                                    }
                                } catch (readError) {
                                    // Check if error is due to component being destroyed
                                    const readErrorMessage = readError?.message || String(readError || '');
                                    if (this._isDestroyed || readErrorMessage.includes('destroyed') || readErrorMessage.includes('Component is destroyed')) {
                                        return;
                                    }
                                    // If reading fails, continue - the reload below will refresh data
                                    console.debug('[BigCommerce Dashboard] Could not read updated display:', readError);
                                }
                            } catch (e) {
                                // Check if error is due to component being destroyed
                                const errorMessage = e?.message || String(e || '');
                                if (this._isDestroyed || errorMessage.includes('destroyed') || errorMessage.includes('Component is destroyed')) {
                                    return;
                                }
                                // If method doesn't exist or fails, just continue - the reload will refresh data
                                console.debug('[BigCommerce Dashboard] Could not force recomputation:', e);
                            }
                        }
                    }
                    
                    // Final check before reloading records
                    if (this._isDestroyed || !this.model || !this.model.records) {
                        return;
                    }
                    
                    // Force recomputation by invalidating and reloading each dashboard record
                    // This will trigger recomputation of computed fields including running_sync_display
                    const reloadPromises = [];
                    for (const record of this.model.records) {
                        if (this._isDestroyed || !this.model || !this.model.records) {
                            break;
                        }
                        
                        if (record && record.resId) {
                            // Invalidate computed fields to force fresh computation
                            if (record.invalidate) {
                                // Invalidate specific computed fields to trigger recomputation
                                record.invalidate(['running_sync_display', 'has_running_syncs', 'running_sync_count', 'running_sync_ids']);
                            }
                            // Then reload - this will trigger recomputation of computed fields
                            if (record.load) {
                                // Wrap load in a promise that checks for destruction
                                reloadPromises.push(
                                    record.load().catch((loadError) => {
                                        // Silently catch load errors if component is destroyed
                                        const loadErrorMessage = loadError?.message || String(loadError || '');
                                        if (!this._isDestroyed && !loadErrorMessage.includes('destroyed') && !loadErrorMessage.includes('Component is destroyed')) {
                                            console.warn('[BigCommerce Dashboard] Failed to reload record:', loadError);
                                        }
                                    })
                                );
                            }
                        }
                    }
                    
                    // Check again before awaiting promises
                    if (this._isDestroyed || !this.model || !this.model.records) {
                        return;
                    }
                    
                    try {
                        await Promise.all(reloadPromises);
                    } catch (promiseError) {
                        // Check if error is due to component being destroyed
                        const errorMessage = promiseError?.message || String(promiseError || '');
                        if (this._isDestroyed || errorMessage.includes('destroyed') || errorMessage.includes('Component is destroyed')) {
                            return;
                        }
                    }
                    
                    // Final check before reloading model and rendering
                    if (this._isDestroyed || !this.model || !this.model.records) {
                        return;
                    }
                    
                    // Also reload the entire model to ensure all computed fields are refreshed
                    try {
                        if (this.model && this.model.load) {
                            await this.model.load();
                        }
                    } catch (modelLoadError) {
                        // Check if error is due to component being destroyed
                        const errorMessage = modelLoadError?.message || String(modelLoadError || '');
                        if (this._isDestroyed || errorMessage.includes('destroyed') || errorMessage.includes('Component is destroyed')) {
                            return;
                        }
                    }
                    
                    // Final check before rendering
                    if (this._isDestroyed) {
                        return;
                    }
                    
                    // Force a re-render to update the UI
                    // Use requestAnimationFrame to ensure DOM updates happen smoothly
                    requestAnimationFrame(() => {
                        if (this._isDestroyed) {
                            return;
                        }
                        // Force update the view to show latest data
                        if (this.model && this.model.notify) {
                            try {
                                this.model.notify();
                            } catch (notifyError) {
                                // Ignore notify errors if component is destroyed
                            }
                        }
                        if (this.render) {
                            try {
                                this.render();
                            } catch (renderError) {
                                // Ignore render errors if component is destroyed
                                const errorMessage = renderError?.message || String(renderError || '');
                                if (!errorMessage.includes('destroyed') && !errorMessage.includes('Component is destroyed')) {
                                    console.warn('[BigCommerce Dashboard] Render error:', renderError);
                                }
                            }
                        }
                    });
                }
            }
        } catch (error) {
            // Check if error is due to component being destroyed
            const errorMessage = error?.message || String(error || '');
            if (this._isDestroyed || errorMessage.includes('destroyed') || errorMessage.includes('Component is destroyed')) {
                // Component was destroyed - silently return
                return;
            }
            console.error('[BigCommerce Dashboard] Error refreshing dashboard:', error);
        }
    },

    _startAutoRefresh() {
        // Don't start if already running
        if (this.autoRefreshInterval) {
            return;
        }

        // Check for running syncs and refresh periodically
        const checkAndRefresh = async () => {
            // Check if component is still mounted before proceeding
            if (this._isDestroyed) {
                this._stopAutoRefresh();
                return;
            }
            
            const model = this.props.resModel;
            if (!model || model !== 'bigcommerce.dashboard') {
                return;
            }

            try {
                // Always refresh the dashboard to get latest data including running syncs
                // Don't check for changes - just refresh to ensure we always have latest progress
                await this._refreshDashboard();
            } catch (error) {
                // Check if error is due to component being destroyed
                const errorMessage = error?.message || String(error || '');
                if (this._isDestroyed || errorMessage.includes('destroyed') || errorMessage.includes('Component is destroyed')) {
                    // Component was destroyed - stop auto-refresh silently
                    this._stopAutoRefresh();
                    return;
                }
                // Only log non-destroyed errors
                console.error('[BigCommerce Dashboard] Error during dashboard auto-refresh:', error);
            }
        };

        // Start auto-refresh every 1 second for dashboard (frequent enough for real-time feel)
        this.autoRefreshInterval = setInterval(checkAndRefresh, 1000);
        
        // Also do an immediate check
        checkAndRefresh();
    },

    _stopAutoRefresh() {
        if (this.autoRefreshInterval) {
            clearInterval(this.autoRefreshInterval);
            this.autoRefreshInterval = null;
        }
    },
});
