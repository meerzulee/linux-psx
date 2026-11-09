// SPDX-License-Identifier: MIT
//! Draws a QR code in console text. A character cell is twice as tall as it is
//! wide, so each cell holds two modules and the modules come out square.
use qrcodegen::{QrCode, QrCodeEcc};

const QUIET_ZONE: i32 = 4;

/// One string per console row, including the quiet zone. Light modules use the
/// foreground colour because the console has no light background slot.
pub fn cells(text: &str) -> Option<Vec<String>> {
    let code = QrCode::encode_text(text, QrCodeEcc::Medium).ok()?;
    let size = code.size() + 2 * QUIET_ZONE;
    let light = |x: i32, y: i32| y < size && !code.get_module(x - QUIET_ZONE, y - QUIET_ZONE);
    let row = |y: i32| -> String {
        (0..size)
            .map(|x| match (light(x, y), light(x, y + 1)) {
                (true, true) => '█',
                (true, false) => '▀',
                (false, true) => '▄',
                (false, false) => ' ',
            })
            .collect()
    };
    Some((0..size).step_by(2).map(row).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_report_address_fits_the_standard_console_grid() {
        let rows = cells("http://192.168.100.200:8080/k7m2x9").unwrap();
        assert_eq!(rows.len(), 19);
        assert!(rows.iter().all(|row| row.chars().count() == 37));
    }

    #[test]
    fn the_quiet_zone_is_light_on_every_side() {
        let rows = cells("http://192.168.1.50:8080/k7m2x9").unwrap();
        assert!(rows[..2].iter().all(|row| row.chars().all(|cell| cell == '█')));
        let (last, middle) = rows.split_last().unwrap();
        for row in middle {
            assert!(row.chars().take(4).all(|cell| cell == '█'));
            assert!(row.chars().rev().take(4).all(|cell| cell == '█'));
        }
        // An odd module count leaves the last row half a cell tall.
        assert!(last.chars().all(|cell| cell == '▀'));
    }
}
