use ashmaize::{Rom, RomGenerationType, hash};
use clap::Parser;
use hex;

pub const MB: usize = 1024 * 1024;
pub const GB: usize = 1024 * MB;

mod tests;

#[derive(Parser, Debug)]
#[command(author, version, about, long_about = None)]
struct Args {
    #[arg(long)]
    address: String,
    #[arg(long)]
    challenge_id: String,
    #[arg(long)]
    difficulty: String, // This is the number of leading zero bits required
    #[arg(long)]
    no_pre_mine: String,
    #[arg(long)]
    latest_submission: String,
    #[arg(long)]
    no_pre_mine_hour: String,
}

pub fn hash_structure_good(hash: &[u8], difficulty_mask: u32) -> bool {
    if hash.len() < 4 {
        return false; // Not enough bytes to apply a u32 mask
    }

    let hash_prefix = u32::from_be_bytes([hash[0], hash[1], hash[2], hash[3]]);
    (hash_prefix & difficulty_mask) == 0
}

pub fn init_rom(no_pre_mine_hex: &str) -> Rom {
    let key = hex::decode(no_pre_mine_hex).unwrap();
    Rom::new(
        &key,
        RomGenerationType::TwoStep {
            pre_size: 16 * MB,
            mixing_numbers: 4,
        },
        1 * GB,
    )
}

fn main() {
    let args = Args::parse();

    // Initialize AshMaize ROM
    let rom = init_rom(&args.no_pre_mine);

    let mut nonce: u64 = 0; // Start with a random nonce or 0

    // Parse difficulty from hex string to u32 mask
    let difficulty_mask = u32::from_str_radix(&args.difficulty, 16).unwrap();

    loop {
        let preimage = format!(
            "{0:016x}{1}{2}{3}{4}{5}{6}",
            nonce,
            args.address,
            args.challenge_id,
            args.difficulty, // This is the hex string, not the number of zero bits
            args.no_pre_mine,
            args.latest_submission,
            args.no_pre_mine_hour
        );

        let hash_result = hash(&preimage.as_bytes(), &rom, 8, 256);

        if hash_structure_good(&hash_result, difficulty_mask) {
            println!("{:016x}", nonce);
            break;
        }

        nonce += 1;
    }
}
