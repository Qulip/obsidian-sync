BEGIN;

CREATE SCHEMA IF NOT EXISTS obsidian;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE obsidian.alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 20260624_0001

CREATE SCHEMA IF NOT EXISTS obsidian;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE obsidian.vaults (
    id BIGSERIAL NOT NULL, 
    vault_id TEXT NOT NULL, 
    name TEXT NOT NULL, 
    description TEXT, 
    default_visibility TEXT DEFAULT 'personal' NOT NULL, 
    is_active BOOLEAN DEFAULT 'true' NOT NULL, 
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_vaults PRIMARY KEY (id), 
    CONSTRAINT ck_vaults_default_visibility CHECK (default_visibility IN ('personal', 'company', 'confidential', 'public')), 
    CONSTRAINT uq_vaults_vault_id UNIQUE (vault_id)
);

CREATE TABLE obsidian.vault_files (
    id BIGSERIAL NOT NULL, 
    vault_pk BIGINT NOT NULL, 
    vault_id TEXT NOT NULL, 
    source_path TEXT NOT NULL, 
    content_hash TEXT NOT NULL, 
    size_bytes BIGINT, 
    mime_type TEXT, 
    file_type TEXT, 
    vectorize BOOLEAN DEFAULT 'false' NOT NULL, 
    status TEXT DEFAULT 'current' NOT NULL, 
    index_status TEXT DEFAULT 'pending' NOT NULL, 
    index_error TEXT, 
    last_synced_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
    last_indexed_at TIMESTAMP WITHOUT TIME ZONE, 
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_vault_files PRIMARY KEY (id), 
    CONSTRAINT ck_vault_files_status CHECK (status IN ('current', 'draft', 'deprecated', 'archived')), 
    CONSTRAINT ck_vault_files_index_status CHECK (index_status IN ('pending', 'indexed', 'failed', 'skipped', 'archived')), 
    CONSTRAINT fk_vault_files_vault_pk_vaults FOREIGN KEY(vault_pk) REFERENCES obsidian.vaults (id) ON DELETE CASCADE, 
    CONSTRAINT uq_vault_files_vault_path UNIQUE (vault_id, source_path)
);

CREATE TABLE obsidian.knowledge_chunks (
    id BIGSERIAL NOT NULL, 
    vault_pk BIGINT NOT NULL, 
    vault_id TEXT NOT NULL, 
    source_path TEXT NOT NULL, 
    chunk_index INTEGER NOT NULL, 
    title TEXT, 
    heading TEXT, 
    heading_path TEXT[], 
    content TEXT NOT NULL, 
    agent_hint TEXT, 
    project TEXT, 
    domain TEXT, 
    type TEXT, 
    status TEXT DEFAULT 'current' NOT NULL, 
    priority TEXT DEFAULT 'medium' NOT NULL, 
    visibility TEXT DEFAULT 'personal' NOT NULL, 
    tags TEXT[], 
    content_hash TEXT NOT NULL, 
    embedding_model TEXT DEFAULT 'bge-m3' NOT NULL, 
    embedding vector(1024), 
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_knowledge_chunks PRIMARY KEY (id), 
    CONSTRAINT ck_knowledge_chunks_type CHECK (type IN ('reference', 'rule', 'decision', 'issue-solution', 'study-note', 'prompt', 'command', 'checklist')), 
    CONSTRAINT ck_knowledge_chunks_status CHECK (status IN ('current', 'draft', 'deprecated', 'archived')), 
    CONSTRAINT ck_knowledge_chunks_priority CHECK (priority IN ('high', 'medium', 'low')), 
    CONSTRAINT ck_knowledge_chunks_visibility CHECK (visibility IN ('personal', 'company', 'confidential', 'public')), 
    CONSTRAINT fk_knowledge_chunks_vault_pk_vaults FOREIGN KEY(vault_pk) REFERENCES obsidian.vaults (id) ON DELETE CASCADE, 
    CONSTRAINT uq_knowledge_chunks_vault_path_chunk UNIQUE (vault_id, source_path, chunk_index)
);

CREATE TABLE obsidian.archived_vault_files (
    id BIGSERIAL NOT NULL, 
    original_id BIGINT, 
    vault_pk BIGINT, 
    vault_id TEXT NOT NULL, 
    source_path TEXT NOT NULL, 
    content_hash TEXT NOT NULL, 
    size_bytes BIGINT, 
    mime_type TEXT, 
    file_type TEXT, 
    archived_reason TEXT NOT NULL, 
    archived_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
    archived_by TEXT DEFAULT 'system', 
    raw_record JSONB, 
    CONSTRAINT pk_archived_vault_files PRIMARY KEY (id)
);

CREATE TABLE obsidian.archived_knowledge_chunks (
    id BIGSERIAL NOT NULL, 
    original_id BIGINT, 
    vault_pk BIGINT, 
    vault_id TEXT NOT NULL, 
    source_path TEXT NOT NULL, 
    chunk_index INTEGER NOT NULL, 
    title TEXT, 
    heading TEXT, 
    heading_path TEXT[], 
    content TEXT, 
    agent_hint TEXT, 
    project TEXT, 
    domain TEXT, 
    type TEXT, 
    status TEXT, 
    priority TEXT, 
    visibility TEXT, 
    tags TEXT[], 
    content_hash TEXT, 
    embedding_model TEXT, 
    embedding vector(1024), 
    archived_reason TEXT NOT NULL, 
    archived_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
    archived_by TEXT DEFAULT 'system', 
    raw_record JSONB, 
    CONSTRAINT pk_archived_knowledge_chunks PRIMARY KEY (id)
);

CREATE TABLE obsidian.search_logs (
    id BIGSERIAL NOT NULL, 
    request_id TEXT NOT NULL, 
    token_id TEXT, 
    vault_pk BIGINT, 
    vault_id TEXT NOT NULL, 
    client_ip TEXT, 
    user_agent TEXT, 
    query TEXT NOT NULL, 
    filters JSONB, 
    top_k INTEGER, 
    result_count INTEGER, 
    latency_ms INTEGER, 
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_search_logs PRIMARY KEY (id), 
    CONSTRAINT fk_search_logs_vault_pk_vaults FOREIGN KEY(vault_pk) REFERENCES obsidian.vaults (id) ON DELETE SET NULL
);

CREATE TABLE obsidian.index_failure_logs (
    id BIGSERIAL NOT NULL, 
    vault_pk BIGINT, 
    vault_id TEXT NOT NULL, 
    source_path TEXT NOT NULL, 
    content_hash TEXT, 
    phase TEXT NOT NULL, 
    error_code TEXT NOT NULL, 
    error_message TEXT NOT NULL, 
    error_details JSONB, 
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_index_failure_logs PRIMARY KEY (id), 
    CONSTRAINT ck_index_failure_logs_phase CHECK (phase IN ('frontmatter', 'chunking', 'embedding', 'database', 'unknown')), 
    CONSTRAINT fk_index_failure_logs_vault_pk_vaults FOREIGN KEY(vault_pk) REFERENCES obsidian.vaults (id) ON DELETE SET NULL
);

CREATE INDEX idx_vault_files_vault_path ON obsidian.vault_files (vault_id, source_path);

CREATE INDEX idx_vault_files_index_status ON obsidian.vault_files (vault_id, index_status);

CREATE INDEX idx_chunks_vault_project_status ON obsidian.knowledge_chunks (vault_id, project, status);

CREATE INDEX idx_chunks_type ON obsidian.knowledge_chunks (type);

CREATE INDEX idx_chunks_priority ON obsidian.knowledge_chunks (priority);

CREATE INDEX idx_chunks_tags ON obsidian.knowledge_chunks USING gin (tags);

CREATE INDEX idx_search_logs_vault_created ON obsidian.search_logs (vault_id, created_at DESC);

CREATE INDEX idx_index_failure_logs_vault_created ON obsidian.index_failure_logs (vault_id, created_at DESC);

INSERT INTO obsidian.alembic_version (version_num) VALUES ('20260624_0001') RETURNING obsidian.alembic_version.version_num;

COMMIT;

