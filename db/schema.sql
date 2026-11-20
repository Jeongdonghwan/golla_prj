CREATE DATABASE IF NOT EXISTS shopa_prj DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE shopa_prj;

-- 사이트(상품군 = 서브도메인) ----------------------------------------------------
CREATE TABLE sites (
  id TINYINT UNSIGNED PRIMARY KEY, host VARCHAR(100) NOT NULL UNIQUE, name VARCHAR(60) NOT NULL, group_name VARCHAR(30) NOT NULL,
  title_sep VARCHAR(5) NOT NULL DEFAULT ' | ',
  naver_verification VARCHAR(64), daum_verification VARCHAR(64), google_verification VARCHAR(64), indexnow_key VARCHAR(64),
  cache_version INT UNSIGNED NOT NULL DEFAULT 1, ranking_version INT UNSIGNED NOT NULL DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE site_settings (site_id TINYINT UNSIGNED, `key` VARCHAR(60), `value` TEXT, PRIMARY KEY(site_id,`key`));
CREATE TABLE redirects (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, site_id TINYINT UNSIGNED NOT NULL,
  from_path VARCHAR(255) NOT NULL, to_path VARCHAR(255) NOT NULL, code SMALLINT DEFAULT 301, UNIQUE KEY uq(site_id,from_path));

-- 상품 종류(키워드의 주인). parent_id = 방식·크기로 쪼갠 하위 종류(F3) ------------------
CREATE TABLE verticals (id SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, site_id TINYINT UNSIGNED NOT NULL, parent_id SMALLINT UNSIGNED NULL,
  slug VARCHAR(40) NOT NULL, name VARCHAR(40) NOT NULL, url_base VARCHAR(50) NOT NULL,   -- '/미니-가습기'
  match_tags JSON,                           -- 하위 종류: 이 태그를 가진 부모 종류 상품이 여기 속한다(F3)
  card JSON,                                 -- 사실 카드(F4, Claude Code 세션 작성·승인본)
  is_active TINYINT(1) DEFAULT 1, UNIQUE KEY uq(site_id,slug), KEY idx_parent(parent_id));

-- 키워드 ----------------------------------------------------------------------
CREATE TABLE keyword_dimensions (id TINYINT UNSIGNED PRIMARY KEY, slug VARCHAR(20) NOT NULL UNIQUE,  -- space,persona,situation,feature,price,intent,question,brand
  name VARCHAR(30) NOT NULL, rank_order TINYINT NOT NULL);
CREATE TABLE keyword_terms (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, vertical_id SMALLINT UNSIGNED NULL, dimension_id TINYINT UNSIGNED NOT NULL,  -- vertical_id NULL = 공통 수식어
  term VARCHAR(80) NOT NULL, slug VARCHAR(80) NOT NULL, canonical_term_id INT UNSIGNED NULL,   -- 동의어는 대표 term 지정(301)
  tag VARCHAR(30) NULL,                      -- 특징 수식어 ↔ 상품 태그(예: '대용량' 페이지 → 대용량 태그 상품)
  volume_pc INT, volume_mobile INT, competition DECIMAL(4,3), metrics_updated_at DATE,
  is_active TINYINT(1) DEFAULT 1, UNIQUE KEY uq(vertical_id,dimension_id,slug), KEY idx_dim(dimension_id), KEY idx_canon(canonical_term_id));
CREATE TABLE keyword_combos (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, site_id TINYINT UNSIGNED NOT NULL, vertical_id SMALLINT UNSIGNED NOT NULL,
  page_type VARCHAR(30) NOT NULL,            -- group_hub, kind_hub, kind_mod(수식어 1~3개), question
  mod1_id INT UNSIGNED NULL, mod2_id INT UNSIGNED NULL, mod3_id INT UNSIGNED NULL, question_id INT UNSIGNED NULL,
  primary_keyword VARCHAR(120) NOT NULL, url_path VARCHAR(255) NULL, parent_combo_id INT UNSIGNED NULL,
  cannibal_group_id INT UNSIGNED NULL, is_group_rep TINYINT(1) DEFAULT 1, grade CHAR(1) NULL,   -- A~D
  volume_total INT DEFAULT 0, competition DECIMAL(4,3), priority_score DECIMAL(8,3) DEFAULT 0,
  status ENUM('draft','generated','review','approved','published','noindex','merged','retired') NOT NULL DEFAULT 'draft',
  content_version INT UNSIGNED NULL, published_at DATETIME NULL, content_updated_at DATETIME NULL, retired_at DATETIME NULL,
  UNIQUE KEY uq_dims(site_id,vertical_id,page_type,mod1_id,mod2_id,mod3_id,question_id),
  UNIQUE KEY uq_url(site_id,url_path), KEY idx_status(site_id,status), KEY idx_parent(parent_combo_id), KEY idx_group(cannibal_group_id), KEY idx_prio(priority_score));

-- 콘텐츠 ----------------------------------------------------------------------
CREATE TABLE page_contents (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, combo_id INT UNSIGNED NOT NULL, version INT UNSIGNED NOT NULL,
  h1 VARCHAR(150), title VARCHAR(160), meta_description VARCHAR(200), blocks JSON NOT NULL, faqs JSON, links JSON,
  model VARCHAR(40), prompt_version VARCHAR(20), uniqueness_score DECIMAL(4,3), lint_result JSON, fact_flags JSON,
  status ENUM('generated','lint_fail','lint_ok','review','approved','rejected') DEFAULT 'generated',
  reviewer VARCHAR(40), reviewed_at DATETIME, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, UNIQUE KEY uq(combo_id,version));
CREATE TABLE pages (                                   -- 렌더 HTML 은 저장하지 않는다. 해시·OG 경로만
  combo_id INT UNSIGNED PRIMARY KEY, rendered_at DATETIME, cache_version INT UNSIGNED, ranking_version INT UNSIGNED,
  content_hash CHAR(32), og_image_path VARCHAR(255), in_sitemap TINYINT(1) DEFAULT 0, noindex TINYINT(1) DEFAULT 0);
CREATE TABLE authors (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, slug VARCHAR(40) NOT NULL UNIQUE, name VARCHAR(40) NOT NULL, role VARCHAR(60), bio TEXT, photo_path VARCHAR(255), links JSON);
CREATE TABLE anchor_variants (combo_id INT UNSIGNED NOT NULL, anchor VARCHAR(120) NOT NULL, PRIMARY KEY(combo_id,anchor));

-- 상품(갈아 끼우는 부품). url = 제휴 링크(naver.me), 고유키. 숫자는 순위 계산용 — 페이지에 쓰지 않는다 ----
CREATE TABLE offers (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, vertical_id SMALLINT UNSIGNED NULL,   -- NULL = 미분류(세션이 분류)
  url VARCHAR(255) NOT NULL UNIQUE, name VARCHAR(200) NOT NULL, advertiser VARCHAR(100) NOT NULL,   -- advertiser = 스토어
  brand VARCHAR(40) NULL, tags JSON, label VARCHAR(40) NULL,            -- label: NEW·기획전 이름 등 CSV 앞머리 표시
  image VARCHAR(500) NULL,                                              -- 썸네일 주소(링크 미리보기 og:image, jobs offers-thumbs). '' = 못 찾음
  fee DECIMAL(5,2) NOT NULL DEFAULT 0, fee_extra DECIMAL(5,2) NOT NULL DEFAULT 0,
  list_price INT NULL, sale_price INT NULL, discount_pct TINYINT NULL, rating DECIMAL(3,2) NULL, review_count INT NULL,
  score DECIMAL(12,3) NOT NULL DEFAULT 0, manual_weight TINYINT NOT NULL DEFAULT 1,
  classified_by ENUM('dict','session','admin') NULL,
  status ENUM('active','inactive') NOT NULL DEFAULT 'active', imported_at DATETIME NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_v(vertical_id,status));
CREATE TABLE offer_imports (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, filename VARCHAR(120), rows_total INT, added INT, removed INT, changed INT,
  unclassified INT, errors JSON, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE clicks (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, click_id CHAR(22) NOT NULL UNIQUE, site_id TINYINT UNSIGNED NOT NULL, offer_id INT UNSIGNED NOT NULL, combo_id INT UNSIGNED NULL,
  slot VARCHAR(30), ua_hash CHAR(32), ip_hash CHAR(32), referrer VARCHAR(255), is_bot TINYINT(1) DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP, KEY idx_offer_dt(offer_id,created_at), KEY idx_combo(combo_id));
CREATE TABLE stats_daily (`date` DATE, site_id TINYINT UNSIGNED, combo_id INT UNSIGNED, offer_id INT UNSIGNED, slot VARCHAR(30),
  impressions INT DEFAULT 0, clicks INT DEFAULT 0, PRIMARY KEY(`date`,site_id,combo_id,offer_id,slot));

-- 측정·운영 --------------------------------------------------------------------
CREATE TABLE gsc_daily (`date` DATE, site_id TINYINT UNSIGNED, page VARCHAR(255), query VARCHAR(200), device VARCHAR(10), clicks INT, impressions INT, ctr DECIMAL(6,4), position DECIMAL(6,2), PRIMARY KEY(`date`,site_id,page,query,device));
CREATE TABLE bot_crawl_daily (`date` DATE, path VARCHAR(255), bot VARCHAR(20), hits INT, status SMALLINT, PRIMARY KEY(`date`,path,bot,status));
CREATE TABLE indexnow_queue (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, site_id TINYINT UNSIGNED, url VARCHAR(1000), reason VARCHAR(20), queued_at DATETIME DEFAULT CURRENT_TIMESTAMP, sent_at DATETIME, response_code SMALLINT, KEY idx(site_id,sent_at));
CREATE TABLE audit_log (id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, actor VARCHAR(40), action VARCHAR(60), target VARCHAR(80), diff JSON, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
