BEGIN;

-- Primero, definir la tabla shopping_list
CREATE TABLE IF NOT EXISTS shopping_list (
  id         BIGSERIAL PRIMARY KEY,
  user_id    TEXT NOT NULL,
  nombre     TEXT NOT NULL CHECK (length(btrim(nombre)) > 0),
  created_at DATE NOT NULL DEFAULT CURRENT_DATE,
  done       BOOLEAN NOT NULL DEFAULT FALSE -- Columna 'done' para la lista
);
-- Índices para shopping_list
CREATE INDEX IF NOT EXISTS idx_shopping_list_user ON shopping_list (user_id);
CREATE INDEX IF NOT EXISTS idx_shopping_list_user_created ON shopping_list (user_id, created_at DESC);

-- Luego, definir la tabla shopping_item (una sola vez)
CREATE TABLE IF NOT EXISTS shopping_item (
  id         BIGSERIAL PRIMARY KEY,
  list_id    BIGINT NOT NULL REFERENCES shopping_list(id) ON DELETE CASCADE,
  nombre     TEXT NOT NULL CHECK (length(btrim(nombre)) > 0),
  cantidad   TEXT NOT NULL CHECK (length(btrim(cantidad)) > 0),
  done       BOOLEAN NOT NULL DEFAULT FALSE, -- Columna 'done' para el item
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Índice para shopping_item
CREATE INDEX IF NOT EXISTS idx_shopping_item_list ON shopping_item (list_id, created_at DESC);

COMMIT;