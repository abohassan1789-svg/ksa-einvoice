-- CRM core PostgreSQL schema.
-- Scope: 7 confirmed local Access CRM tables only.
-- Excludes 4 linked/temp Access tables that need owner review.

CREATE TABLE IF NOT EXISTS places (
    place_id integer PRIMARY KEY,
    place_number varchar(50)
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id integer PRIMARY KEY,
    phone_number varchar(40),
    customer_name varchar(100),
    place_area_feddan integer,
    area_number varchar(30),
    building varchar(30),
    unit_number varchar(30),
    floor_number varchar(60),
    installment_duration_years integer,
    remaining_installments integer,
    installment_amount integer,
    legacy_area_number_2 varchar(30)
);

CREATE TABLE IF NOT EXISTS employees (
    employee_id integer PRIMARY KEY,
    phone_number varchar(40) NOT NULL,
    employee_name varchar(100),
    governorate varchar(100),
    district_center varchar(100),
    village varchar(100)
);

CREATE TABLE IF NOT EXISTS case_statuses (
    case_status_id integer PRIMARY KEY,
    case_status_name varchar(90)
);

CREATE TABLE IF NOT EXISTS daily_followups (
    daily_followup_id integer PRIMARY KEY,
    sequence_number integer DEFAULT 0,
    follow_up_date timestamp,
    customer_id integer,
    employee_id integer,
    case_status_id integer,
    notes varchar(150),
    follow_up_month smallint,
    follow_up_year smallint,
    legacy_month_year_code varchar(243),
    contact_count integer DEFAULT 1,
    installment_duration_years double precision,
    remaining_installments double precision,
    installment_amount double precision,
    installment_type varchar(50),
    buyer_name varchar(90),
    buyer_phone_number varchar(90),
    unit_sale_amount double precision,
    seller_commission_amount double precision,
    buyer_commission_amount double precision,
    CONSTRAINT fk_daily_followups_customer
        FOREIGN KEY (customer_id)
        REFERENCES customers (customer_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    CONSTRAINT fk_daily_followups_employee
        FOREIGN KEY (employee_id)
        REFERENCES employees (employee_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    CONSTRAINT fk_daily_followups_case_status
        FOREIGN KEY (case_status_id)
        REFERENCES case_statuses (case_status_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS company_settings (
    company_name_ar varchar(80),
    general_manager_name varchar(80),
    email_address varchar(25),
    mobile_number varchar(25),
    website_url varchar(60),
    address varchar(80),
    commercial_registry_number varchar(25),
    tax_number varchar(25),
    image_path varchar(255),
    company_name_en varchar(80),
    civil_defense_license_number varchar(50),
    civil_defense_license_start_date timestamp,
    civil_defense_license_end_date timestamp,
    municipality_license_number varchar(50),
    municipality_license_start_date timestamp,
    municipality_license_end_date timestamp
);

CREATE TABLE IF NOT EXISTS app_users (
    username varchar(100) PRIMARY KEY,
    password_legacy_plain_text varchar(20) NOT NULL,
    can_access_statuses boolean DEFAULT false,
    can_access_customers boolean DEFAULT false,
    can_access_employees boolean DEFAULT false,
    can_access_daily_followups boolean DEFAULT false,
    can_access_reports boolean DEFAULT false,
    can_access_users boolean DEFAULT false,
    can_access_places_or_leaves boolean DEFAULT false,
    can_access_reports_secondary boolean DEFAULT false,
    can_access_notification_report boolean DEFAULT false,
    can_access_salary_receipt boolean DEFAULT false,
    can_access_end_of_service_reward boolean DEFAULT false,
    can_access_cars boolean DEFAULT false,
    can_access_users_secondary boolean DEFAULT false,
    legacy_permission_flag_14 boolean DEFAULT false,
    user_role_level integer DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_customers_phone_number
    ON customers (phone_number);

CREATE INDEX IF NOT EXISTS idx_daily_followups_customer_id
    ON daily_followups (customer_id);

CREATE INDEX IF NOT EXISTS idx_daily_followups_employee_id
    ON daily_followups (employee_id);

CREATE INDEX IF NOT EXISTS idx_daily_followups_case_status_id
    ON daily_followups (case_status_id);

CREATE INDEX IF NOT EXISTS idx_daily_followups_follow_up_date
    ON daily_followups (follow_up_date);

CREATE INDEX IF NOT EXISTS idx_daily_followups_buyer_name
    ON daily_followups (buyer_name);
