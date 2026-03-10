# Temporary file to check indentation
                            if update_result is True:
                                self.products_synced += 1
                                _logger.info(f"✓ Successfully synced inventory for {product.name} (BC ID: {product.bigcommerce_id}) - Products Synced: {self.products_synced}")
                            else:
                                # Get more specific error message

