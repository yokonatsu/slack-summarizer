#!/usr/bin/env python3
# https://github.com/masuidrive/slack-summarizer
# by [masuidrive](https://twitter.com/masuidrive) @ [Bloom&Co., Inc.](https://www.bloom-and-co.com/) 2023- [APACHE LICENSE, 2.0](https://www.apache.org/licenses/LICENSE-2.0)
import os
import re
import time
import pytz
from slack_sdk.errors import SlackApiError
from slack_sdk import WebClient
from datetime import datetime, timedelta
import openai
from openai.error import InvalidRequestError

#以下の3つはご自身の環境で取得し書き換える必要があります。
#openai.api_key = "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"
# APIトークンとチャンネルIDを設定する
#TOKEN = "xoxb-7805923970389-7811523068980-847Ai1uV8CV2hXAdM6OW3jSH"
#CHANNEL_ID = "C07Q5LWCM33"

OPEN_AI_TOKEN = str(os.environ.get('OPEN_AI_TOKEN')).strip()
SLACK_BOT_TOKEN = str(os.environ.get('SLACK_BOT_TOKEN')).strip()
CHANNEL_ID = str(os.environ.get('SLACK_POST_CHANNEL_ID')).strip()
openai.api_key = OPEN_AI_TOKEN

# OpenAIのAPIを使って要約を行う
def summarize(text):
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        temperature=0.5,
        messages=[
            {"role": "system", "content": "チャットログのフォーマットは発言者: 本文\\nになっている。\\nは改行を表しています。これを踏まえて指示に従います"},
            {"role": "user", "content": f"下記のチャットログを箇条書きで要約してください。1行ずつの説明ではありません。全体として短く。お礼とかは除外して。\n\n{text}"}
        ]
    )
    return response["choices"][0]["message"]['content']

# 取得する期間を計算する
HOURS_BACK = 25
JST = pytz.timezone('Asia/Tokyo')
now = datetime.now(JST)
yesterday = now - timedelta(hours=HOURS_BACK)
start_time = datetime(yesterday.year, yesterday.month, yesterday.day,
                      yesterday.hour, yesterday.minute, yesterday.second)
end_time = datetime(now.year, now.month, now.day,
                    now.hour, now.minute, now.second)

# Slack APIクライアントを初期化する
client = WebClient(token=SLACK_BOT_TOKEN)

# ユーザーIDからユーザー名に変換するために、ユーザー情報を取得する
try:
    users_info = client.users_list()
    users = users_info['members']

except SlackApiError as e:
    print("Error : {}".format(e))
    exit(1)

print(f"Users: {users}")

# チャンネルIDからチャンネル名に変換するために、チャンネル情報を取得する
try:
    channels_info = client.conversations_list(
        types="public_channel,private_channel",
        limit=500
    )

    channels = [channel for channel in channels_info['channels']
            if not channel["is_archived"] and channel["is_channel"]]
    channels = sorted(channels, key=lambda x: int(re.findall(
        r'\d+', x["name"])[0]) if re.findall(r'\d+', x["name"]) else float('inf'))
except SlackApiError as e:
    print("Error : {}".format(e))
    exit(1)

# 指定したチャンネルの履歴を取得する
def load_messages(channel_id, ts):
    result = None
    result = client.conversations_replies(ts=ts,
                                          channel=channel_id,
                                          oldest=start_time.timestamp(),
                                          latest=end_time.timestamp()
                                          )

    # messages = result["messages"]
    messages = list(filter(lambda m: "subtype" not in m, result["messages"]))

    if len(messages) < 1:
        return None

    messages_text = []

    while result["has_more"]:
        result = client.conversations_replies(ts=ts,
                                              channel=channel_id,
                                              oldest=start_time.timestamp(),
                                              latest=end_time.timestamp(),
                                              cursor=result["response_metadata"]["next_cursor"]
                                              )
        messages.extend(result["messages"])
    for message in messages[::-1]:
        if "bot_id" in message:
            continue
        if message["text"].strip() == '':
            continue
        # ユーザーIDからユーザー名に変換する
        user_id = message['user']
        sender_name = None
        for user in users:
            if user['id'] == user_id:
                sender_name = user['name']
                break
        if sender_name is None:
            sender_name = user_id

        # テキスト取り出し
        text = message["text"].replace("\n", "\\n")

        # メッセージ中に含まれるユーザーIDやチャンネルIDを名前やチャンネル名に展開する
        matches = re.findall(r"<@[A-Z0-9]+>", text)
        for match in matches:
            user_id = match[2:-1]
            user_name = None
            for user in users:
                if user['id'] == user_id:
                    user_name = user['name']
                    break
            if user_name is None:
                user_name = user_id
            text = text.replace(match, f"@{user_name} ")

        matches = re.findall(r"<#[A-Z0-9]+>", text)
        for match in matches:
            channel_id = match[2:-1]
            channel_name = None
            for channel in channels:
                if channel['id'] == channel_id:
                    channel_name = channel['name']
                    break
            if channel_name is None:
                channel_name = channel_id
            text = text.replace(match, f"#{channel_name} ")
        messages_text.append(f"{sender_name}: {text}")
    if len(messages_text) == 0:
        return None
    else:
        return messages_text

result_text = []
for channel in channels:

    print(channel["name"])

    try:
        threads = client.conversations_history(channel=channel["id"],
                                               oldest=start_time.timestamp(),
                                               latest=end_time.timestamp())
    except SlackApiError as e:
        if e.response['error'] == 'not_in_channel':
            response = client.conversations_join(
                channel=channel["id"]
            )
            if not response["ok"]:
                raise SlackApiError("conversations_join() failed")
            time.sleep(5)  # チャンネルにjoinした後、少し待つ

            threads = client.conversations_history(
                channel=channel["id"],
                oldest=start_time.timestamp(),
                latest=end_time.timestamp()
            )
        else:
            print("Error : {}".format(e))
            continue

    messages_in_channel = []

    if not threads["messages"]:
        continue

    result_text.append(f"----\n<#{channel['id']}>\n")
    for thread in threads["messages"]:
        if thread:
            messages = load_messages(channel["id"], thread["ts"])
            if messages:
                messages_in_channel.extend(messages[::-1])

    # summarize関数の呼び出しを削除
    try:
        # result_text.append(summarize(messages_in_channel))
        result_text.append("\n\n".join(messages_in_channel))

    except InvalidRequestError as e:
        print(e)

title = (f"{yesterday.strftime('%Y-%m-%d')}の要約テスト")

text = title+"\n\n"+"\n\n".join(result_text)

response = client.chat_postMessage(
    channel=CHANNEL_ID,
    text=text
)
print("Message posted: ", response["ts"])
