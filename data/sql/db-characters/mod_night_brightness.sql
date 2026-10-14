-- mod-night-brightness: the night brightness level each account picked with .night. Accounts on
-- the realm's own night (level 3) have no row.
CREATE TABLE IF NOT EXISTS `mod_night_brightness` (
    `account` INT UNSIGNED NOT NULL,
    `level`   TINYINT UNSIGNED NOT NULL,
    PRIMARY KEY (`account`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
