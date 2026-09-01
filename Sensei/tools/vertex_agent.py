import argparse
import os
import sys

import vertexai
from vertexai.generative_models import GenerativeModel


PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "project-62238635-aae4-41f4-880")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL_NAME = os.getenv("VERTEX_MODEL", "gemini-2.5-flash")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="*", help="Prompt text")
    parser.add_argument("--model", default=MODEL_NAME)
    args = parser.parse_args()

    prompt = " ".join(args.prompt).strip()
    if not prompt:
        prompt = sys.stdin.read().strip()

    if not prompt:
        print("Prompt boş.")
        sys.exit(1)

    vertexai.init(project=PROJECT_ID, location=LOCATION)
    model = GenerativeModel(args.model)

    response = model.generate_content(prompt)
    print(response.text)


if __name__ == "__main__":
    main()
