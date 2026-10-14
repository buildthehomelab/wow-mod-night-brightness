-- mod-night-brightness: takes the accounts' night brightness levels out of the characters
-- database again. Run by hand after the module is removed from the server; the updater does not
-- run this folder.
DROP TABLE IF EXISTS `mod_night_brightness`;
DELETE FROM `updates` WHERE `name` = 'mod_night_brightness.sql';
