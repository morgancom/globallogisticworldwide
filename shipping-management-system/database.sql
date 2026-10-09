-- Create database with proper character set
CREATE DATABASE IF NOT EXISTS shipping_db 
CHARACTER SET utf8mb4 
COLLATE utf8mb4_unicode_ci;

USE shipping_db;

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(80) UNIQUE NOT NULL,
    email VARCHAR(120) UNIQUE NOT NULL,
    password_hash VARCHAR(256) NOT NULL,
    role ENUM('admin', 'super_admin') DEFAULT 'admin',
    full_name VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP NULL,
    INDEX idx_users_username (username),
    INDEX idx_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Packages table
CREATE TABLE IF NOT EXISTS packages (
    id INT PRIMARY KEY AUTO_INCREMENT,
    tracking_code VARCHAR(20) UNIQUE NOT NULL,
    sender_name VARCHAR(100) NOT NULL,
    sender_address TEXT NOT NULL,
    sender_phone VARCHAR(20) NOT NULL,
    sender_email VARCHAR(100) NOT NULL,
    recipient_name VARCHAR(100) NOT NULL,
    recipient_address TEXT NOT NULL,
    recipient_phone VARCHAR(20) NOT NULL,
    recipient_email VARCHAR(100) NOT NULL,
    weight DECIMAL(10,2) NOT NULL,
    dimensions VARCHAR(50),
    declared_value DECIMAL(10,2),
    package_type ENUM('documents', 'electronics', 'fragile', 'general') DEFAULT 'general',
    shipping_type ENUM('express', 'standard', 'freight') DEFAULT 'standard',
    shipping_method ENUM('air', 'ground', 'sea') DEFAULT 'ground',
    estimated_delivery DATETIME,
    pickup_address TEXT NOT NULL,
    destination_address TEXT NOT NULL,
    current_location_lat DECIMAL(10,8),
    current_location_lng DECIMAL(11,8),
    status ENUM('pending', 'in_transit', 'on_hold', 'delivered', 'cancelled') DEFAULT 'pending',
    created_by INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NULL ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES users(id),
    INDEX idx_packages_tracking (tracking_code),
    INDEX idx_packages_status (status),
    INDEX idx_packages_created (created_at),
    INDEX idx_packages_recipient_email (recipient_email),
    INDEX idx_packages_estimated (estimated_delivery)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Route milestones table
CREATE TABLE IF NOT EXISTS route_milestones (
    id INT PRIMARY KEY AUTO_INCREMENT,
    package_id INT NOT NULL,
    sequence_order INT NOT NULL,
    location_name VARCHAR(200) NOT NULL,
    latitude DECIMAL(10,8) NOT NULL,
    longitude DECIMAL(11,8) NOT NULL,
    estimated_hours_from_start INT,
    is_completed BOOLEAN DEFAULT FALSE,
    completed_at TIMESTAMP NULL,
    FOREIGN KEY (package_id) REFERENCES packages(id) ON DELETE CASCADE,
    INDEX idx_milestones_package (package_id),
    INDEX idx_milestones_sequence (package_id, sequence_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Location history table
CREATE TABLE IF NOT EXISTS location_history (
    id INT PRIMARY KEY AUTO_INCREMENT,
    package_id INT NOT NULL,
    latitude DECIMAL(10,8) NOT NULL,
    longitude DECIMAL(11,8) NOT NULL,
    location_name VARCHAR(200),
    status VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (package_id) REFERENCES packages(id) ON DELETE CASCADE,
    INDEX idx_location_history_package (package_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Hold records table
CREATE TABLE IF NOT EXISTS hold_records (
    id INT PRIMARY KEY AUTO_INCREMENT,
    package_id INT NOT NULL,
    reason VARCHAR(255) NOT NULL,
    hold_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    hold_end TIMESTAMP NULL,
    created_by INT,
    FOREIGN KEY (package_id) REFERENCES packages(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES users(id),
    INDEX idx_hold_records_package (package_id, hold_start),
    INDEX idx_hold_records_active (package_id, hold_end)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Notifications table
CREATE TABLE IF NOT EXISTS notifications (
    id INT PRIMARY KEY AUTO_INCREMENT,
    package_id INT NOT NULL,
    recipient_email VARCHAR(100),
    recipient_phone VARCHAR(20),
    type ENUM('tracking_created', 'status_update', 'on_hold', 'delay', 'delivered'),
    message TEXT,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status ENUM('pending', 'sent', 'failed') DEFAULT 'pending',
    FOREIGN KEY (package_id) REFERENCES packages(id) ON DELETE CASCADE,
    INDEX idx_notifications_package (package_id),
    INDEX idx_notifications_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Delivery confirmations table
CREATE TABLE IF NOT EXISTS delivery_confirmations (
    id INT PRIMARY KEY AUTO_INCREMENT,
    package_id INT UNIQUE NOT NULL,
    signature_path VARCHAR(255),
    received_by VARCHAR(100),
    delivery_photo_path VARCHAR(255),
    confirmed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (package_id) REFERENCES packages(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Insert default admin user (password: Admin@123)
-- Note: You'll need to update this with a proper password hash
INSERT INTO users (username, email, password_hash, full_name, role) VALUES 
('admin', 'admin@shipping.com', 'pbkdf2:sha256:600000$xyz', 'System Administrator', 'super_admin')
ON DUPLICATE KEY UPDATE id=id;