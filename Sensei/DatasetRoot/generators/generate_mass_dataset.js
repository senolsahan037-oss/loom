import { spawnSync } from "child_process";

const limitArgIndex = process.argv.indexOf("--limit");
const offsetArgIndex = process.argv.indexOf("--offset");

const limit = limitArgIndex !== -1
  ? Number(process.argv[limitArgIndex + 1])
  : 100;

const offset = offsetArgIndex !== -1
  ? Number(process.argv[offsetArgIndex + 1])
  : 0;

const genres = [
  "West Coast Hip-Hop",
  "Boom Bap",
  "Trip Hop",
  "Trap",
  "Drill",
  "Arabesk Rap",
  "Phonk",
  "Memphis Rap",
  "Cloud Rap",
  "Lo-Fi Hip Hop",
  "House",
  "Tech House",
  "Deep House",
  "Techno",
  "Melodic Techno",
  "Ambient",
  "Synthwave",
  "Cinematic Hip Hop",
  "Jazz Hop",
  "Dark Trap",
  "Brutal Hip-Hop",
  "Industrial Hip-Hop",
  "Aggressive Boom Bap"
];

const variations = [
  "minimal",
  "mainstream",
  "experimental",
  "instrumental",
  "vocal-focused"
];

const moods = [
  "dark",
  "emotional",
  "aggressive",
  "cinematic",
  "uplifting"
];

const sampleInstructions = [
  "modern sample chopping techniques",
  "channels named by sample chop technique",
  "micro chops, phrase chops, reverse chops, vocal chops, stutter chops",
  "sample-based arrangement with texture bed and one-shot hits"
];

const energyProfiles = [
  "slow build",
  "hook-heavy",
  "club energy",
  "sparse verses with strong hooks",
  "cinematic rise and fall"
];

let count = 0;

for (const genre of genres) {
  for (const variation of variations) {
    for (const mood of moods) {
      for (const energy of energyProfiles) {
        if (count < offset) {
          count++;
          continue;
        }

        if (count >= offset + limit) {
          console.log(`Mass dataset generation finished. Created target: ${count}`);
          process.exit(0);
        }

        const sampleLayer = genre.toLowerCase().includes("hip-hop") || genre.toLowerCase().includes("boom") || genre.toLowerCase().includes("trip") || genre.toLowerCase().includes("phonk")
          ? ` Sample layer: ${sampleInstructions[count % sampleInstructions.length]}. Kanal isimleri mümkünse sample parçalama tekniği isimleriyle kurulsun.`
          : "";

        const prompt = `${genre} aranjmanı üret. Variation: ${variation}. Mood: ${mood}. Energy profile: ${energy}. ArrangementGPS ortak dataset formatında üret.${sampleLayer}`;

        console.log(`\n[${count - offset + 1}/${limit}] global:${count + 1} ${prompt}`);

        const result = spawnSync(
          "node",
          [
            "SubverseDataset/api_generated/arrangementgps/generate_one.js",
            prompt
          ],
          {
            stdio: "inherit",
            timeout: 90000
          }
        );

        if (result.status !== 0) {
          console.error(`Failed prompt: ${prompt}`);
        }

        count++;
      }
    }
  }
}

console.log(`Mass dataset generation finished. Total created: ${count}`);
