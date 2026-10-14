-- mod-night-brightness: takes the module's table out of the world database again. Run by hand
-- after the module is removed from the server; the updater does not run this folder.
DROP TABLE IF EXISTS `mod_night_brightness_light`;
DELETE FROM `updates` WHERE `name` = 'mod_night_brightness_light.sql';
