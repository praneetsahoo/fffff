-- OpsIntel database schema (MySQL 8 on Amazon RDS)
-- Three tables:
--   uploads        one row per uploaded file (where it is in S3 + its row counts)
--   orders         the clean, validated business records
--   rejected_rows  every problem found, one row per problem, so nothing is silently dropped

CREATE TABLE IF NOT EXISTS uploads (
    upload_id        INT AUTO_INCREMENT PRIMARY KEY,
    file_name        VARCHAR(255) NOT NULL,
    file_hash        CHAR(64)     NOT NULL,          -- SHA-256 of the file: same file can't load twice
    s3_raw_key       VARCHAR(500) NOT NULL,          -- original file, never changed
    s3_clean_key     VARCHAR(500) NOT NULL,          -- cleaned rows as CSV
    s3_rejected_key  VARCHAR(500) NOT NULL,          -- refused rows with reasons as CSV
    total_rows       INT NOT NULL,
    loaded_rows      INT NOT NULL,
    rejected_rows    INT NOT NULL,
    duplicate_rows   INT NOT NULL,
    fixed_values     INT NOT NULL,                   -- values repaired automatically
    uploaded_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_uploads_hash UNIQUE (file_hash)
);

CREATE TABLE IF NOT EXISTS orders (
    order_id       VARCHAR(20)   PRIMARY KEY,        -- business key: the same order can't be stored twice
    upload_id      INT           NOT NULL,
    order_date     DATE          NOT NULL,
    order_month    CHAR(7)       NOT NULL,           -- 'YYYY-MM', derived, makes monthly queries simple
    region         VARCHAR(20)   NOT NULL,
    state          VARCHAR(50)   NOT NULL,
    city           VARCHAR(50)   NOT NULL,
    category       VARCHAR(50)   NOT NULL,
    product        VARCHAR(100)  NOT NULL,
    quantity       INT           NOT NULL,
    unit_price     DECIMAL(12,2) NOT NULL,
    revenue        DECIMAL(14,2) NOT NULL,           -- derived: quantity * unit_price
    status         VARCHAR(20)   NOT NULL,
    delivery_days  INT           NULL,               -- only known once delivered/returned
    is_late        TINYINT(1)    NULL,               -- derived: delivery_days > 5
    CONSTRAINT fk_orders_upload FOREIGN KEY (upload_id) REFERENCES uploads (upload_id),
    INDEX idx_orders_date (order_date),
    INDEX idx_orders_month (order_month),
    INDEX idx_orders_region_category (region, category)
);

CREATE TABLE IF NOT EXISTS rejected_rows (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    upload_id     INT          NOT NULL,
    line_number   INT          NOT NULL,             -- line in the original file (header = line 1)
    issue_column  VARCHAR(50)  NOT NULL,
    issue_type    VARCHAR(20)  NOT NULL,             -- missing | wrong_type | invalid_value | duplicate
    message       VARCHAR(255) NOT NULL,
    raw_data      TEXT         NOT NULL,             -- the original row, as uploaded
    CONSTRAINT fk_rejected_upload FOREIGN KEY (upload_id) REFERENCES uploads (upload_id),
    INDEX idx_rejected_upload_type (upload_id, issue_type)
);
