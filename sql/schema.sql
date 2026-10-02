-- Esquema normalizado (3FN) del centralizador de trámites.
-- Un registro = un trámite realizado. Los catálogos evitan variantes de nombre.
PRAGMA foreign_keys = ON;

CREATE TABLE oficina (
    id     INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL UNIQUE
);

CREATE TABLE tramite_tipo (
    id     INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL UNIQUE
);

CREATE TABLE encargado (
    id     INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL UNIQUE
);

CREATE TABLE registro (
    folio          TEXT PRIMARY KEY,                       -- TR-AAAA-NNNNN, único
    fecha          TEXT NOT NULL
                   CHECK (fecha GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    tramite_id     INTEGER NOT NULL REFERENCES tramite_tipo(id),
    encargado_id   INTEGER NOT NULL REFERENCES encargado(id),
    oficina_id     INTEGER NOT NULL REFERENCES oficina(id),
    ciudadano_hash TEXT,                                   -- seudónimo, nunca el nombre
    estatus        TEXT NOT NULL
                   CHECK (estatus IN ('Concluido','En proceso','Pendiente','Rechazado','Sin estatus')),
    archivo_origen TEXT NOT NULL,
    hoja_origen    TEXT NOT NULL
);

CREATE INDEX idx_registro_fecha     ON registro(fecha);
CREATE INDEX idx_registro_tramite   ON registro(tramite_id);
CREATE INDEX idx_registro_encargado ON registro(encargado_id);
CREATE INDEX idx_registro_oficina   ON registro(oficina_id);

-- Auditoría: qué pasó con cada archivo.
CREATE TABLE calidad_etl (
    archivo                 TEXT NOT NULL,
    hoja                    TEXT NOT NULL,
    filas_leidas            INTEGER NOT NULL,
    filas_cargadas          INTEGER NOT NULL,
    filas_rechazadas        INTEGER NOT NULL,
    folios_duplicados       INTEGER NOT NULL,
    tramites_corregidos     INTEGER NOT NULL,   -- nombre reconocido por similitud
    encargados_sin_asignar  INTEGER NOT NULL,
    sin_estatus             INTEGER NOT NULL,
    PRIMARY KEY (archivo, hoja)
);

-- Filas que NO entraron, con el motivo (nada se descarta en silencio).
CREATE TABLE rechazado (
    archivo    TEXT NOT NULL,
    hoja       TEXT NOT NULL,
    fila       INTEGER NOT NULL,     -- fila del Excel original
    motivo     TEXT NOT NULL,
    dato_crudo TEXT
);
