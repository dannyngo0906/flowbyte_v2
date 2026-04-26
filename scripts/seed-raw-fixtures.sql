-- Sample raw data for dbt staging smoke test.
-- Inserts 1 row into each P0 raw table with realistic Haravan-shaped JSONB.
-- Idempotent: deletes the seed run_id first.

DO $$
DECLARE
    seed_run UUID := '00000000-0000-0000-0000-000000000001';
BEGIN
    DELETE FROM raw.haravan_orders     WHERE source_run_id = seed_run;
    DELETE FROM raw.haravan_customers  WHERE source_run_id = seed_run;
    DELETE FROM raw.haravan_products   WHERE source_run_id = seed_run;
    DELETE FROM raw.haravan_locations  WHERE source_run_id = seed_run;

    INSERT INTO raw.haravan_orders (id, payload, updated_at, source_run_id) VALUES
    (1001,
     jsonb_build_object(
        'id', 1001, 'name', '#1001', 'order_number', '1001',
        'customer_id', 501, 'location_id', 1,
        'currency', 'VND',
        'financial_status', 'paid', 'fulfillment_status', 'fulfilled',
        'subtotal_price', '500000', 'total_discounts', '50000',
        'total_tax', '0', 'total_shipping', '30000',
        'total_price', '480000', 'total_refunded', '0',
        'created_at', '2026-04-25T08:00:00Z', 'updated_at', '2026-04-25T10:00:00Z',
        'closed_at', null, 'cancelled_at', null,
        'line_items', jsonb_build_array(
            jsonb_build_object('id', 9001, 'variant_id', 7001, 'quantity', 2, 'price', '250000')
        ),
        'refunds', jsonb_build_array(),
        'transactions', jsonb_build_array(
            jsonb_build_object('id', 6001, 'kind', 'sale', 'status', 'success',
                               'gateway', 'cod', 'amount', '480000',
                               'created_at', '2026-04-25T08:00:00Z')
        )
     ),
     '2026-04-25T10:00:00Z'::timestamptz, seed_run),
    (1002,
     jsonb_build_object(
        'id', 1002, 'name', '#1002', 'order_number', '1002',
        'customer_id', 501, 'location_id', 1,
        'currency', 'VND',
        'financial_status', 'partially_refunded', 'fulfillment_status', 'fulfilled',
        'subtotal_price', '300000', 'total_discounts', '0',
        'total_tax', '0', 'total_shipping', '20000',
        'total_price', '320000', 'total_refunded', '100000',
        'created_at', '2026-04-26T08:00:00Z', 'updated_at', '2026-04-26T12:00:00Z',
        'closed_at', null, 'cancelled_at', null,
        'line_items', jsonb_build_array(
            jsonb_build_object('id', 9002, 'variant_id', 7001, 'quantity', 1, 'price', '300000')
        ),
        -- Refund with 1 nested transaction → exercises BOTH order_tx and refund_tx UNION branches.
        'refunds', jsonb_build_array(
            jsonb_build_object('id', 5001, 'created_at', '2026-04-26T11:00:00Z',
                               'note', 'customer requested partial', 'reason', 'wrong size',
                               'amount', '100000', 'restock', true,
                               'refund_line_items', jsonb_build_array(),
                               'transactions', jsonb_build_array(
                                   jsonb_build_object('id', 6003, 'kind', 'refund', 'status', 'success',
                                                      'gateway', 'cod', 'amount', '100000',
                                                      'created_at', '2026-04-26T11:00:00Z')
                               ))
        ),
        'transactions', jsonb_build_array(
            jsonb_build_object('id', 6002, 'kind', 'sale', 'status', 'success',
                               'gateway', 'cod', 'amount', '320000',
                               'created_at', '2026-04-26T08:00:00Z')
        )
     ),
     '2026-04-26T12:00:00Z'::timestamptz, seed_run);

    INSERT INTO raw.haravan_customers (id, payload, updated_at, source_run_id) VALUES
    (501,
     jsonb_build_object(
        'id', 501, 'email', 'a@example.com', 'phone', '+84901234567',
        'first_name', 'An', 'last_name', 'Nguyen',
        'state', 'enabled', 'accepts_marketing', true,
        'orders_count', 3, 'total_spent', '1500000',
        'tags', 'vip',
        'created_at', '2025-01-01T00:00:00Z', 'updated_at', '2026-04-25T10:00:00Z',
        'addresses', jsonb_build_array(),
        'default_address', null
     ),
     '2026-04-25T10:00:00Z'::timestamptz, seed_run);

    INSERT INTO raw.haravan_products (id, payload, updated_at, source_run_id) VALUES
    (3001,
     jsonb_build_object(
        'id', 3001, 'title', 'Plain Tee', 'handle', 'plain-tee',
        'vendor', 'Acme', 'product_type', 'apparel',
        'status', 'active', 'tags', 'tee,unisex',
        'created_at', '2025-06-01T00:00:00Z', 'updated_at', '2026-04-25T10:00:00Z',
        'published_at', '2025-06-01T00:00:00Z',
        'variants', jsonb_build_array(
            jsonb_build_object('id', 7001, 'title', 'S', 'sku', 'tee-s', 'barcode', null,
                               'price', '250000', 'compare_at_price', null,
                               'inventory_quantity', 50,
                               'option1', 'S', 'option2', null, 'option3', null,
                               'inventory_management', 'haravan', 'inventory_policy', 'deny',
                               'weight', '0.2', 'weight_unit', 'kg',
                               'created_at', '2025-06-01T00:00:00Z',
                               'updated_at', '2026-04-25T10:00:00Z')
        ),
        'images', jsonb_build_array(),
        'options', jsonb_build_array()
     ),
     '2026-04-25T10:00:00Z'::timestamptz, seed_run);

    INSERT INTO raw.haravan_locations (id, payload, updated_at, source_run_id) VALUES
    (1,
     jsonb_build_object(
        'id', 1, 'name', 'HCM Warehouse',
        'address1', '123 Le Lai', 'address2', null,
        'city', 'HCM', 'province', 'HCM', 'country', 'Vietnam', 'country_code', 'VN',
        'zip', '700000', 'phone', '+842839991111',
        'active', true,
        'created_at', '2025-01-01T00:00:00Z', 'updated_at', '2026-04-25T10:00:00Z'
     ),
     '2026-04-25T10:00:00Z'::timestamptz, seed_run);
END $$;
