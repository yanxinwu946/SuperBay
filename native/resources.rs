use std::{
    fs::File,
    io::{self, Read},
    path::Path,
};

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum Format {
    Folder,
    Document,
    Image,
    Media,
    Archive,
    Other,
}

impl Format {
    pub fn from_path(path: &Path, directory: bool) -> Self {
        if directory {
            return Self::Folder;
        }
        match extension(path).as_str() {
            "pdf" | "doc" | "docx" | "xls" | "xlsx" | "ppt" | "pptx" | "odt" | "ods" | "odp"
            | "rtf" | "epub" => Self::Document,
            "png" | "jpg" | "jpeg" | "gif" | "webp" | "bmp" | "ico" | "svg" | "tif" | "tiff"
            | "avif" | "heic" => Self::Image,
            "mp4" | "mkv" | "mov" | "avi" | "webm" | "mp3" | "flac" | "wav" | "ogg" | "m4a" => {
                Self::Media
            }
            "zip" | "7z" | "rar" | "tar" | "gz" | "xz" | "zst" | "bz2" => Self::Archive,
            _ if plain_text(path) => Self::Document,
            _ => Self::Other,
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Folder => "文件夹",
            Self::Document => "文档",
            Self::Image => "图片",
            Self::Media => "音视频",
            Self::Archive => "压缩包",
            Self::Other => "其他文件",
        }
    }
}

pub fn extension(path: &Path) -> String {
    path.extension()
        .unwrap_or_default()
        .to_string_lossy()
        .to_ascii_lowercase()
}

pub fn plain_text(path: &Path) -> bool {
    matches!(
        extension(path).as_str(),
        "md" | "markdown"
            | "txt"
            | "log"
            | "csv"
            | "tsv"
            | "json"
            | "xml"
            | "yaml"
            | "yml"
            | "toml"
            | "ini"
            | "cfg"
            | "conf"
            | "rs"
            | "py"
            | "js"
            | "ts"
            | "go"
            | "java"
            | "cs"
            | "sh"
            | "cmd"
            | "bat"
            | "ps1"
            | "sql"
            | "html"
            | "css"
    )
}

pub fn read_text(path: &Path, limit: usize) -> io::Result<Option<(String, bool)>> {
    if !plain_text(path) {
        return Ok(None);
    }
    let mut bytes = Vec::new();
    File::open(path)?
        .take((limit + 1) as u64)
        .read_to_end(&mut bytes)?;
    let truncated = bytes.len() > limit;
    bytes.truncate(limit);
    Ok(decode_text(&bytes).map(|content| (content, truncated)))
}

pub fn read_full_text(path: &Path) -> io::Result<Option<String>> {
    if !plain_text(path) {
        return Ok(None);
    }
    std::fs::read(path).map(|bytes| decode_text(&bytes))
}

fn decode_text(bytes: &[u8]) -> Option<String> {
    let utf16 = bytes
        .strip_prefix(&[0xff, 0xfe])
        .map(|bytes| (bytes, false))
        .or_else(|| bytes.strip_prefix(&[0xfe, 0xff]).map(|bytes| (bytes, true)));
    let content = if let Some((bytes, big_endian)) = utf16 {
        let units: Vec<_> = bytes
            .chunks_exact(2)
            .map(|pair| {
                if big_endian {
                    u16::from_be_bytes([pair[0], pair[1]])
                } else {
                    u16::from_le_bytes([pair[0], pair[1]])
                }
            })
            .collect();
        String::from_utf16_lossy(&units)
    } else if bytes.contains(&0) {
        return None;
    } else {
        String::from_utf8_lossy(bytes.strip_prefix(&[0xef, 0xbb, 0xbf]).unwrap_or(bytes))
            .into_owned()
    };
    Some(content)
}

pub fn size_label(size: u64) -> String {
    for (unit, bytes) in [
        ("GiB", 1024u64.pow(3)),
        ("MiB", 1024u64.pow(2)),
        ("KiB", 1024),
    ] {
        if size >= bytes {
            return format!("{:.1} {unit}", size as f64 / bytes as f64);
        }
    }
    format!("{size} B")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn preview_decodes_utf16_and_limits_output() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("sample.txt");
        let bytes: Vec<_> = [0xfe, 0xff]
            .into_iter()
            .chain("一二三".encode_utf16().flat_map(u16::to_be_bytes))
            .collect();
        fs::write(&path, bytes).unwrap();
        assert_eq!(read_text(&path, 6).unwrap(), Some(("一二".into(), true)));
        assert_eq!(read_full_text(&path).unwrap().as_deref(), Some("一二三"));
        assert_eq!(
            Format::from_path(Path::new("photo.PNG"), false),
            Format::Image
        );
        assert_eq!(
            Format::from_path(Path::new("report.docx"), false),
            Format::Document
        );
    }
}
