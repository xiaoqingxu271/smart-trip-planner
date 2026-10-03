-- ============================================================
-- 智能旅行助手 数据库初始化脚本（可重复执行，幂等）
-- 用法：mysql -uroot -proot < backend/sql/init.sql
-- 或由应用启动时自动执行（backend/app/storage/db.py）
-- ============================================================

CREATE DATABASE IF NOT EXISTS trip_planner
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE trip_planner;

-- 用户表：AUTH_MODE=user 时的账号体系
CREATE TABLE IF NOT EXISTS users (
  id            BIGINT       AUTO_INCREMENT PRIMARY KEY,
  username      VARCHAR(32)  NOT NULL UNIQUE,     -- 3-32 位字母/数字/下划线
  password_hash VARCHAR(255) NOT NULL,            -- pbkdf2_sha256$轮数$盐$摘要
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

-- 行程表：每次多智能体规划成功后自动入库
CREATE TABLE IF NOT EXISTS trips (
  id           BIGINT       AUTO_INCREMENT PRIMARY KEY,
  request_json JSON         NOT NULL,              -- 原始规划请求（目的地/偏好/预算…）
  plan_json    JSON         NOT NULL,              -- 完整 TripPlan（含图片URL）
  destination  VARCHAR(64)  NOT NULL,              -- 目的地（列表页筛选用）
  days         INT          NOT NULL,
  grand_total  DECIMAL(10,2),                      -- 预算总计（卡片展示用）
  cover_url    VARCHAR(500) NULL,                  -- 列表页封面图（写入时从 plan 提取，免拉全量 JSON）
  themes       VARCHAR(200) NULL,                  -- 列表页主题标签（| 分隔）
  summary      VARCHAR(200) NULL,                  -- 列表页总览摘要
  starred      TINYINT(1)   NOT NULL DEFAULT 0,    -- 是否收藏
  is_seed      TINYINT(1)   NOT NULL DEFAULT 0,    -- 是否为内置示例作品
  user_id      BIGINT       NULL,                  -- 所属用户（NULL=无主，全员可读；见 README 可见性规则）
  parent_id    BIGINT       NULL,                  -- 重规划版本链：指向被反馈的原行程
  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_created (created_at),
  INDEX idx_starred (starred, created_at),
  INDEX idx_seed (is_seed, created_at),
  INDEX idx_user (user_id),
  INDEX idx_parent (parent_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;
