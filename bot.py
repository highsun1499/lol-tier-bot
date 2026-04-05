import discord
from discord.ext import commands, tasks
import aiohttp
import json
import re
import os
import html
import datetime
from datetime import time, timezone, timedelta
import random
import traceback
import xml.etree.ElementTree as ET # ★ 레딧 RSS 파싱을 위해 반드시 import 구역에 추가해 주세요!

# ================= [ 설정 구역 ] =================
RIOT_API_KEY = os.getenv("RIOT_API_KEY")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

# 아래 채널 ID를 본인 서버에 맞게 설정해 주세요!
NEWS_CHANNEL_ID = 1480944831600656384  
VOTE_CHANNEL_ID = 1484797598241128598  
YT_NOTI_CHANNEL_ID = 1487481812874825879
REDDIT_NOTI_CHANNEL_ID = 1487488570791821443

KST = timezone(timedelta(hours=9))
SCHEDULED_VOTE_TIME = time(hour=13, minute=0, second=0, tzinfo=KST)

TIER_DATA = {
    "Challenger": 0xf4c874, "Grandmaster": 0xc64444, "Master": 0x9d5ca3,
    "Diamond": 0x576bce, "Emerald": 0x2da161, "Platinum": 0x4e9996,
    "Gold": 0xcd8837, "Silver": 0x80989d, "Bronze": 0x8c513a,
    "Iron": 0x51484a, "Unranked": 0x000000
}
TIER_LIST = list(TIER_DATA.keys())
pending_users = {}

def log(message):
    now = datetime.datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now}] {message}", flush=True)

# =================[ 봇 클래스 정의 ] =================
class LoLBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True 
        super().__init__(command_prefix='!', intents=intents)
        self.session = None

    async def setup_hook(self):
        # 레딧은 봇 User-Agent를 고유하게 적어주는 것을 권장/요구합니다.
        self.session = aiohttp.ClientSession(headers={
            "User-Agent": "linux:lol-support-bot:v1.0 (by /u/YourRedditUsername)"
        })
        log("공용 HTTP 세션 생성 및 봇 준비 완료")
        
    async def close(self):
        if self.session:
            await self.session.close()
        await super().close()

bot = LoLBot()

# =================[ 공통 유틸리티 함수 ] =================
async def get_puuid(name_with_tag):
    if "#" not in name_with_tag: return None
    try:
        name, tag = name_with_tag.split("#", 1)
        url = f"https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name.strip()}/{tag.strip()}?api_key={RIOT_API_KEY}"
        async with bot.session.get(url) as resp:
            if resp.status == 200:
                return (await resp.json()).get('puuid')
    except Exception as e:
        log(f"PUUID 조회 에러: {e}")
    return None

async def get_recent_posted_links(channel, limit=100):
    """채널 최근 히스토리에서 봇이 올린 임베드 URL 목록을 한 번에 가져옴"""
    links =[]
    async for msg in channel.history(limit=limit):
        if msg.author == bot.user and msg.embeds and msg.embeds[0].url:
            links.append(msg.embeds[0].url)
    return links

# ================= [ 핵심 기능 1: 공식 홈페이지 뉴스 ] =================
async def fetch_and_post_news():
    log("롤 뉴스 체크 중...")
    url = "https://www.leagueoflegends.com/ko-kr/news/"
    
    # 롤 공홈용 일반 User-Agent
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    try:
        async with bot.session.get(url, headers=headers) as resp:
            if resp.status != 200: return
            raw_html = await resp.text()
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', raw_html)
            if not match: return
            
            data = json.loads(match.group(1))
            blades = data.get('props', {}).get('pageProps', {}).get('page', {}).get('blades', [])
            articles = next((b['items'] for b in blades if b.get('type') == 'articleCardGrid'), [])[:10]
            articles.reverse()

            channel = await bot.fetch_channel(NEWS_CHANNEL_ID)
            posted_links = await get_recent_posted_links(channel, limit=100)
            
            for art in articles:
                link = art.get('action', {}).get('payload', {}).get('url', '')
                if not link: continue
                if not link.startswith('http'): link = "https://www.leagueoflegends.com" + link
                
                if link in posted_links: continue

                title = art.get('title', '새로운 소식')
                desc = re.sub(r'<[^>]+>', '', art.get('description', {}).get('body', '')).strip()
                img = html.unescape(art.get('media', {}).get('url', '')).strip()
                
                pub = art.get('publishedAt', '')
                date_text = "새 소식"
                if pub:
                    dt = datetime.datetime.fromisoformat(pub.replace('Z', '+00:00')).astimezone(KST)
                    date_text = dt.strftime("%Y년 %m월 %d일 %H:%M")

                embed = discord.Embed(title=title, url=link, description=desc[:100], color=0xc28f2c)
                if img.startswith('http'): embed.set_image(url=img)
                embed.set_footer(text=date_text)
                await channel.send(embed=embed)
                log(f"뉴스 포스팅: {title}")
                posted_links.append(link) 
                
    except Exception as e: log(f"뉴스 에러: {e}")

# =================[ 핵심 기능 2: 유튜브 재생목록 ] =================
async def fetch_and_post_youtube():
    log("유튜브 영상 체크 중...")
    UPLOADS_PLAYLIST_ID = "UUooLkG0FfrkPBQsSuC95L6w"
    
    if not YOUTUBE_API_KEY:
        log("유튜브 API 키 부재")
        return
        
    url = f"https://www.googleapis.com/youtube/v3/playlistItems?key={YOUTUBE_API_KEY}&playlistId={UPLOADS_PLAYLIST_ID}&part=snippet&maxResults=10"
    try:
        async with bot.session.get(url) as resp:
            if resp.status != 200: return
            videos = (await resp.json()).get('items',[])
            videos.reverse()

            channel = await bot.fetch_channel(YT_NOTI_CHANNEL_ID)
            posted_links = await get_recent_posted_links(channel, limit=100)
            
            for vid in videos:
                v_id = vid.get('snippet', {}).get('resourceId', {}).get('videoId')
                if not v_id: continue
                
                v_url = f"https://www.youtube.com/watch?v={v_id}"
                if v_url in posted_links: continue

                title = html.unescape(vid['snippet']['title'])
                desc = vid['snippet']['description'][:100] + "..."
                
                thumbnails = vid['snippet']['thumbnails']
                img = thumbnails.get('maxres', thumbnails.get('high', {})).get('url', '')
                
                dt = datetime.datetime.fromisoformat(vid['snippet']['publishedAt'].replace('Z', '+00:00')).astimezone(KST)

                embed = discord.Embed(title=title, url=v_url, description=desc, color=0xFF0000)
                if img: embed.set_image(url=img)
                embed.set_footer(text=f"{dt.strftime('%Y년 %m월 %d일 %H:%M')}")
                
                await channel.send(embed=embed)
                log(f"유튜브 포스팅: {title}")
                posted_links.append(v_url)
                
    except Exception as e: log(f"유튜브 에러: {e}")

# =================[ 핵심 기능 3: 레딧(Reddit) 스크래핑 (16:9 강제 크롭 & 썸네일 완벽 추출판) ] =================
async def fetch_and_post_reddit():
    log("레딧(Reddit) 공식 RSS 확인 중...")
    
    url = "https://www.reddit.com/r/leagueoflegends/search.rss?q=flair%3A%22Riot+official%22&restrict_sr=on&sort=new&t=all"
    headers = {
        "User-Agent": "linux:lol-support-bot-rss:v_perfect_16_9 (by /u/friendlybot)"
    }
    
    # 기본 와이드 배너 (사진/링크가 없는 순수 텍스트 공지사항을 위한 롤 오피셜 무적 배경)
    DEFAULT_WIDE_BANNER = "https://i.ytimg.com/vi/RprbAMOPsH0/maxresdefault.jpg"

    try:
        async with bot.session.get(url, headers=headers) as resp:
            if resp.status != 200:
                log(f"레딧 RSS 연결 실패 ({resp.status})")
                return
            
            raw_xml = await resp.text()            
            root = ET.fromstring(raw_xml)
            namespace = {'atom': 'http://www.w3.org/2005/Atom'}
            entries = root.findall('atom:entry', namespace)
            
            target_entries = entries[:10]
            target_entries.reverse()

            channel = await bot.fetch_channel(REDDIT_NOTI_CHANNEL_ID)
            posted_links = await get_recent_posted_links(channel, limit=100)

            for entry in target_entries:
                link_element = entry.find('atom:link', namespace)
                link = link_element.attrib['href'] if link_element is not None else ""
                
                if not link or link in posted_links: continue
                
                title_node = entry.find('atom:title', namespace)
                title = title_node.text if title_node is not None else "새로운 글"
                title = html.unescape(title)
                
                content_node = entry.find('atom:content', namespace)
                content_html = content_node.text if content_node is not None else ""
                
                # ---[★ 16:9 완벽 썸네일 탐색기]---
                img_url = ""
                
                # 1순위: 유튜브 영상 탐색 
                yt_match = re.search(r'href=["\']https://(?:www\.)?youtu(?:be\.com/watch\?v=|\.be/)([^"\'&?]+)', content_html)
                if yt_match:
                    yt_id = yt_match.group(1)
                    img_url = f"https://img.youtube.com/vi/{yt_id}/hqdefault.jpg"
                
                # 2순위: 롤 공식 홈페이지 링크를 파고들어 썸네일 직접 강탈!! (패치노트 이미지 해결사)
                if not img_url:
                    article_match = re.search(r'<a href=["\']([^"\']+)["\']>\[link\]</a>', content_html)
                    if article_match:
                        article_url = html.unescape(article_match.group(1))
                        if "leagueoflegends.com" in article_url or "lolesports.com" in article_url:
                            try:
                                headers_riot = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                                async with bot.session.get(article_url, headers=headers_riot, timeout=3) as a_resp:
                                    if a_resp.status == 200:
                                        a_html = await a_resp.text()
                                        og_m = re.search(r'<meta property="og:image" content=["\']([^"\']+)["\']', a_html)
                                        if og_m:
                                            img_url = html.unescape(og_m.group(1))
                            except Exception as og_err:
                                log(f"OG 이미지 추출 에러: {og_err}")

                # 3순위: 글 작성자가 직접 올린 레딧 자체 이미지(i.redd.it)
                if not img_url:
                    direct_match = re.search(r'href=["\'](https://i\.redd\.it/[^"\']+)["\']', content_html)
                    if direct_match:
                        img_url = html.unescape(direct_match.group(1))
                
                # 4순위: 압축된 프리뷰 이미지 원본 복구
                if not img_url:
                    prev_match = re.search(r'(https://(?:preview|external-preview)\.redd\.it/[^"\'?]+)', content_html)
                    if prev_match and "thumbs" not in prev_match.group(1):
                        img_url = prev_match.group(1).replace("external-preview", "i").replace("preview", "i")
                        
                # 5순위: 썸네일도 없고, 주소도 없고, 오직 텍스트만 있는 글 -> 공식 배경 배너!
                if not img_url:
                    img_url = DEFAULT_WIDE_BANNER

                # === 텍스트 클리닝 (찌꺼기 무자비하게 완전 소각) ===
                desc = re.sub(r'<br\s*/?>', '\n', content_html)
                desc = re.sub(r'<[^>]+>', '', desc)
                desc = html.unescape(desc).strip()
                
                desc = re.sub(r'(?i)submitted by\s+/u/[\w-]+', '', desc).strip()
                desc = desc.replace('[link]', '').replace('[comments]', '').strip()
                desc = re.sub(r'https?://[^\s\n]+', '', desc).strip() 
                
                if len(desc) > 100:
                    desc = desc[:100] + "..."
                    
                if not desc:
                    desc = "여기를 클릭하여 본문을 확인하세요."
                
                pub_node = entry.find('atom:updated', namespace)
                date_text = "Reddit (Riot Official)"
                if pub_node is not None and pub_node.text:
                    try:
                        dt = datetime.datetime.fromisoformat(pub_node.text.replace('Z', '+00:00'))
                        dt_korea = dt.astimezone(KST)
                        date_text = f"Reddit (Riot Official) • {dt_korea.strftime('%Y년 %m월 %d일 %H:%M')}"
                    except:
                        pass

                embed = discord.Embed(title=title, url=link, description=desc, color=0xFF4500)
                
                # =========================================================================
                # ★[최종 병기: 어떤 사진이든 16:9로 "확대시켜 꽉 차게 잘라내는(Cover)" 마법] ★
                # fit=cover 옵션 덕분에 회색 여백 바가 생기지 않습니다. 
                # 핸드폰 화면 비율 사진조차도 확대해서 아름다운 16:9 프레임에 꽉 맞게 담아냅니다.
                # =========================================================================
                forced_16_9_url = f"https://wsrv.nl/?url={img_url}&w=1920&h=1080&fit=cover"
                embed.set_image(url=forced_16_9_url) 
                
                embed.set_footer(text=date_text)
                
                try:
                    await channel.send(embed=embed)
                    log(f"레딧 포스팅 완료: {title[:20]}...")
                    posted_links.append(link)
                except Exception as send_e:
                    log(f"레딧 전송 에러: {send_e}")

    except Exception as e:
        log(f"레딧 파싱 중 에러 발생: {e}")
        traceback.print_exc()

# =================[ 자동 루프 & 이벤트 ] =================
@tasks.loop(minutes=60)
async def main_loop():
    # 3가지 플랫폼 (뉴스, 유튜브, 레딧) 스위치 온!
    await fetch_and_post_news()
    await fetch_and_post_youtube()
    await fetch_and_post_reddit()

@tasks.loop(time=SCHEDULED_VOTE_TIME)
async def daily_vote_loop():
    try:
        channel = await bot.fetch_channel(VOTE_CHANNEL_ID)
        date_str = datetime.datetime.now(KST).strftime("%Y년 %m월 %d일")
        poll = discord.Poll(question=f"🎮 {date_str} 오늘 게임하실 건가요?", duration=timedelta(hours=11))
        for t, e in[("TOP", "🛡️"), ("JGL", "⚔️"), ("MID", "🔥"), ("SUP", "✨"), ("ADC", "🏹"), ("미정", "❓"), ("불참", "❌")]:
            poll.add_answer(text=t, emoji=e)
        await channel.send(poll=poll)
        log(f"투표 게시 완료 ({date_str})")
    except Exception as e: log(f"투표 에러: {e}")

@bot.event
async def on_ready():
    log(f"봇 로그인: {bot.user.name}")
    if not main_loop.is_running(): main_loop.start()
    if not daily_vote_loop.is_running(): daily_vote_loop.start()

# ================= [ 명령어 구역 ] =================
@bot.command()
async def 인증(ctx, *, name=None):
    if not name or "#" not in name:
        return await ctx.send("❌ 사용법: `!인증 소환사명#태그`")
    
    icon_id = random.randint(0, 28)
    pending_users[ctx.author.id] = {"name": name, "icon": icon_id}
    url = f"https://ddragon.leagueoflegends.com/cdn/14.1.1/img/profileicon/{icon_id}.png"
    
    embed = discord.Embed(title="🛡️ 계정 인증", description=f"**{name}**님, 아이콘을 변경 후 `!확인`을 입력하세요.", color=0x5865F2)
    embed.set_thumbnail(url=url)
    await ctx.send(embed=embed)

@bot.command()
async def 확인(ctx):
    user = pending_users.get(ctx.author.id)
    if not user: return await ctx.send("❌ 먼저 `!인증`을 해주세요.")
    
    puuid = await get_puuid(user["name"])
    if not puuid: return await ctx.send("❌ 계정을 찾을 수 없습니다.")

    async with bot.session.get(f"https://kr.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={RIOT_API_KEY}") as r:
        if r.status == 200 and (await r.json()).get('profileIconId') == user["icon"]:
            await ctx.send(f"✅ 인증 성공! 이제 `!갱신 {user['name']}`을 입력하세요.")
            del pending_users[ctx.author.id]
        else:
            await ctx.send("❌ 아이콘이 일치하지 않습니다.")

@bot.command()
async def 갱신(ctx, *, name=None):
    if not name or "#" not in name: return await ctx.send("❌ 사용법: `!갱신 소환사명#태그`")
    
    puuid = await get_puuid(name)
    if not puuid: return await ctx.send("❌ 계정을 찾을 수 없습니다.")

    async with bot.session.get(f"https://kr.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}?api_key={RIOT_API_KEY}") as r:
        if r.status == 200:
            data = await r.json()
            tier = next((e['tier'] for e in data if e['queueType'] == 'RANKED_SOLO_5x5'), "Unranked").capitalize()
            role = discord.utils.get(ctx.guild.roles, name=tier)
            
            if not role: return await ctx.send(f"❌ '{tier}' 역할이 서버에 없습니다.")
            
            await ctx.author.remove_roles(*[r for r in ctx.author.roles if r.name in TIER_LIST])
            await ctx.author.add_roles(role)
            await ctx.send(f"🔄 **{ctx.author.display_name}**님, **{tier}** 갱신 완료!")

bot.run(DISCORD_TOKEN)
