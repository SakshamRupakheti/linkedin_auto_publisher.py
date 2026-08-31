import os
import datetime
import requests
import openai

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINKEDIN_ACCESS_TOKEN = os.getenv("LINKEDIN_ACCESS_TOKEN")
LINKEDIN_PERSON_URN = os.getenv("LINKEDIN_PERSON_URN")

WEEKLY_TOPICS = [
    "Practical prompt engineering patterns for software developers",
    "How regression to the mean explains unexpected business spikes",
    "Lessons learned from setting up lightweight automated workflows",
    "Why system architecture matters when scaling AI applications",
    "Key productivity strategies for juggling multiple tech projects",
    "The evolution of modern web standards and APIs",
    "Weekly reflection on building tools and staying consistent"
]

def generate_post(topic: str) -> str:
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    prompt = f"""
    Write an engaging, professional LinkedIn post about: "{topic}".
    Guidelines:
    - Catchy opening line.
    - Short paragraphs separated by line breaks.
    - 3-4 bullet points highlighting actionable insights.
    - End with a thought-provoking question to invite comments.
    - Include 3 relevant hashtags.
    """
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )
    return response.choices[0].message.content

def publish_to_linkedin(post_text: str):
    url = "https://api.linkedin.com/rest/posts"
    headers = {
        "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "LinkedIn-Version": "202401",
        "X-Restli-Protocol-Version": "2.0.0"
    }
    payload = {
        "author": f"urn:li:person:{LINKEDIN_PERSON_URN}",
        "commentary": post_text,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": []
        },
        "lifecycleState": "PUBLISHED"
    }
    
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 201:
        print("Successfully published post to LinkedIn!")
    else:
        print(f"Failed to post ({response.status_code}): {response.text}")

if __name__ == "__main__":
    today_index = datetime.datetime.today().weekday()
    current_topic = WEEKLY_TOPICS[today_index]
    
    print(f"Generating post for Day {today_index + 1}: '{current_topic}'...")
    draft = generate_post(current_topic)
    publish_to_linkedin(draft)
