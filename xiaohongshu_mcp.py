from typing import Any, List, Dict, Optional
import asyncio
import json
import os
import pandas as pd
from datetime import datetime
from playwright.async_api import async_playwright
from fastmcp import FastMCP
import logging

logging.basicConfig(level=logging.INFO)

# 初始化 FastMCP 服务器
mcp = FastMCP("xiaohongshu_scraper")

# 全局变量
BROWSER_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_data")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

# 确保目录存在
os.makedirs(BROWSER_DATA_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# 用于存储浏览器上下文，以便在不同方法之间共享
browser_context = None
main_page = None
is_logged_in = False
context_restart_lock = asyncio.Lock()

async def ensure_browser():
    """确保浏览器已启动并登录，并保证context可用"""
    global browser_context, main_page, is_logged_in
    async with context_restart_lock:
        try:
            if browser_context is not None:
                try:
                    page = await browser_context.new_page()
                    await page.close()
                except Exception:
                    browser_context = None  # context已失效，需重启
        except Exception:
            browser_context = None

        if browser_context is None:
            playwright_instance = await async_playwright().start()
            browser_context = await playwright_instance.chromium.launch_persistent_context(
                user_data_dir=BROWSER_DATA_DIR,
                headless=False,  # 非隐藏模式，方便用户登录
                viewport={"width": 1280, "height": 800},
                timeout=60000
            )
            # 只保留main_page，其余全部关闭
            if browser_context.pages:
                main_page = browser_context.pages[0]
                # 关闭多余标签页
                for p in browser_context.pages[1:]:
                    try:
                        await p.close()
                    except Exception as e:
                        logging.warning(f"关闭多余标签页出错: {e}")
            else:
                main_page = await browser_context.new_page()
            main_page.set_default_timeout(60000)
            is_logged_in = False  # 新context需重新判断登录
        # 检查登录状态
        if not is_logged_in:
            # 只在首次启动时goto主页
            await main_page.goto("https://www.xiaohongshu.com", timeout=60000)
            await asyncio.sleep(3)
            login_elements = await main_page.query_selector_all('text="登录"')
            if login_elements:
                return False  # 需要登录
            else:
                is_logged_in = True
                return True  # 已登录
        return True

@mcp.tool()
async def login() -> str:
    """登录小红书账号"""
    global is_logged_in
    
    await ensure_browser()
    
    if is_logged_in:
        return "已登录小红书账号"
    
    # 访问小红书登录页面
    await main_page.goto("https://www.xiaohongshu.com", timeout=60000)
    await asyncio.sleep(3)
    
    # 查找登录按钮并点击
    login_elements = await main_page.query_selector_all('text="登录"')
    if login_elements:
        await login_elements[0].click()
        
        # 提示用户手动登录
        message = "请在打开的浏览器窗口中完成登录操作。登录成功后，系统将自动继续。"
        
        # 等待用户登录成功
        max_wait_time = 180  # 等待3分钟
        wait_interval = 5
        waited_time = 0
        
        while waited_time < max_wait_time:
            # 检查是否已登录成功
            still_login = await main_page.query_selector_all('text="登录"')
            if not still_login:
                is_logged_in = True
                await asyncio.sleep(2)  # 等待页面加载
                return "登录成功！"
            
            # 继续等待
            await asyncio.sleep(wait_interval)
            waited_time += wait_interval
        
        return "登录等待超时。请重试或手动登录后再使用其他功能。"
    else:
        is_logged_in = True
        return "已登录小红书账号"

@mcp.tool()
async def search_notes(keywords: str, limit: int = 5) -> str:
    """根据关键词搜索笔记
    
    Args:
        keywords: 搜索关键词
        limit: 返回结果数量限制
    """
    for attempt in range(2):
        try:
            login_status = await ensure_browser()
            if not login_status:
                return "请先登录小红书账号"
            page = await browser_context.new_page()
            logging.info(f"[{datetime.now()}] 新建标签页: {page}, tool: search_notes, keywords: {keywords}")
            try:
                search_url = f"https://www.xiaohongshu.com/search_result?keyword={keywords}"
                logging.info(f"[{datetime.now()}] search_notes: page.goto({search_url}) 开始")
                await page.goto(search_url, timeout=60000)
                logging.info(f"[{datetime.now()}] search_notes: page.goto({search_url}) 完成")
                await asyncio.sleep(5)
                await asyncio.sleep(5)
                page_html = await page.content()
                logging.info(f"页面HTML片段: {page_html[10000:10500]}...")
                logging.info("尝试获取帖子卡片...")
                post_cards = await page.query_selector_all('section.note-item')
                logging.info(f"找到 {len(post_cards)} 个帖子卡片")
                if not post_cards:
                    post_cards = await page.query_selector_all('div[data-v-a264b01a]')
                    logging.info(f"使用备用选择器找到 {len(post_cards)} 个帖子卡片")
                post_links = []
                post_titles = []
                for card in post_cards:
                    try:
                        link_element = await card.query_selector('a[href*="/search_result/"]')
                        if not link_element:
                            continue
                        href = await link_element.get_attribute('href')
                        if href and '/search_result/' in href:
                            full_url = f"https://www.xiaohongshu.com{href}"
                            post_links.append(full_url)
                            try:
                                card_html = await card.inner_html()
                                logging.info(f"卡片HTML片段: {card_html[:200]}...")
                                title_element = await card.query_selector('div.footer a.title span')
                                if title_element:
                                    title = await title_element.text_content()
                                    logging.info(f"找到标题(方法1): {title}")
                                else:
                                    title_element = await card.query_selector('a.title span')
                                    if title_element:
                                        title = await title_element.text_content()
                                        logging.info(f"找到标题(方法2): {title}")
                                    else:
                                        text_elements = await card.query_selector_all('span')
                                        potential_titles = []
                                        for text_el in text_elements:
                                            text = await text_el.text_content()
                                            if text and len(text.strip()) > 5:
                                                potential_titles.append(text.strip())
                                        if potential_titles:
                                            title = max(potential_titles, key=len)
                                            logging.info(f"找到可能的标题(方法3): {title}")
                                        else:
                                            all_text = await card.evaluate('el => Array.from(el.querySelectorAll("*")).map(node => node.textContent).filter(text => text && text.trim().length > 5)')
                                            if all_text and len(all_text) > 0:
                                                title = max(all_text, key=len)
                                                logging.info(f"找到可能的标题(方法4): {title}")
                                            else:
                                                title = "未知标题"
                                                logging.info("无法找到标题，使用默认值'未知标题'")
                                if not title or title.strip() == "":
                                    title = "未知标题"
                                    logging.info("获取到的标题为空，使用默认值'未知标题'")
                            except Exception as e:
                                logging.exception(f"获取标题时出错: {str(e)}")
                                title = "未知标题"
                            post_titles.append(title)
                    except Exception as e:
                        logging.exception(f"处理帖子卡片时出错: {str(e)}")
                unique_posts = []
                seen_urls = set()
                for url, title in zip(post_links, post_titles):
                    if url not in seen_urls:
                        seen_urls.add(url)
                        unique_posts.append({"url": url, "title": title})
                unique_posts = unique_posts[:limit]
                if unique_posts:
                    result = "搜索结果：\n\n"
                    for i, post in enumerate(unique_posts, 1):
                        result += f"{i}. {post['title']}\n   链接: {post['url']}\n\n"
                    return result
                else:
                    return f"未找到与\"{keywords}\"相关的笔记"
            finally:
                logging.info(f"[{datetime.now()}] search_notes: 关闭标签页: {page}")
                await page.close()
        except Exception as e:
            if attempt == 0 and ("context" in str(e).lower() or "browser has been closed" in str(e).lower() or "Target page" in str(e)):
                continue
            return f"搜索笔记时出错: {str(e)}"

@mcp.tool()
async def get_note_content(url: str) -> str:
    """获取笔记内容
    
    Args:
        url: 笔记 URL
    """
    for attempt in range(2):
        try:
            login_status = await ensure_browser()
            if not login_status:
                return "请先登录小红书账号"
            page = await browser_context.new_page()
            logging.info(f"[{datetime.now()}] 新建标签页: {page}, tool: get_note_content, url: {url}")
            try:
                await page.goto(url, timeout=60000)
                await asyncio.sleep(10)
                await page.evaluate('''
                    () => {
                        window.scrollTo(0, document.body.scrollHeight);
                        setTimeout(() => { window.scrollTo(0, document.body.scrollHeight / 2); }, 1000);
                        setTimeout(() => { window.scrollTo(0, 0); }, 2000);
                    }
                ''')
                await asyncio.sleep(3)
                try:
                    logging.info("打印页面结构片段用于分析")
                    page_structure = await page.evaluate('''
                        () => {
                            const noteContent = document.querySelector('.note-content');
                            const detailDesc = document.querySelector('#detail-desc');
                            const commentArea = document.querySelector('.comments-container, .comment-list');
                            return {
                                hasNoteContent: !!noteContent,
                                hasDetailDesc: !!detailDesc,
                                hasCommentArea: !!commentArea,
                                noteContentHtml: noteContent ? noteContent.outerHTML.slice(0, 500) : null,
                                detailDescHtml: detailDesc ? detailDesc.outerHTML.slice(0, 500) : null,
                                commentAreaFirstChild: commentArea ? 
                                    (commentArea.firstElementChild ? commentArea.firstElementChild.outerHTML.slice(0, 500) : null) : null
                            };
                        }
                    ''')
                    logging.info(f"页面结构分析: {json.dumps(page_structure, ensure_ascii=False, indent=2)}")
                except Exception as e:
                    logging.exception(f"打印页面结构时出错: {str(e)}")
                post_content = {}
                try:
                    logging.info("尝试获取标题 - 方法1：使用id选择器")
                    title_element = await page.query_selector('#detail-title')
                    if title_element:
                        title = await title_element.text_content()
                        post_content["标题"] = title.strip() if title else "未知标题"
                        logging.info(f"方法1获取到标题: {post_content['标题']}")
                    else:
                        logging.info("方法1未找到标题元素")
                        post_content["标题"] = "未知标题"
                except Exception as e:
                    logging.exception(f"方法1获取标题出错: {str(e)}")
                    post_content["标题"] = "未知标题"
                if post_content["标题"] == "未知标题":
                    try:
                        logging.info("尝试获取标题 - 方法2：使用class选择器")
                        title_element = await page.query_selector('div.title')
                        if title_element:
                            title = await title_element.text_content()
                            post_content["标题"] = title.strip() if title else "未知标题"
                            logging.info(f"方法2获取到标题: {post_content['标题']}")
                        else:
                            logging.info("方法2未找到标题元素")
                    except Exception as e:
                        logging.exception(f"方法2获取标题出错: {str(e)}")
                if post_content["标题"] == "未知标题":
                    try:
                        logging.info("尝试获取标题 - 方法3：使用JavaScript")
                        title = await page.evaluate('''
                            () => {
                                const selectors = [
                                    '#detail-title',
                                    'div.title',
                                    'h1',
                                    'div.note-content div.title'
                                ];
                                for (const selector of selectors) {
                                    const el = document.querySelector(selector);
                                    if (el && el.textContent.trim()) {
                                        return el.textContent.trim();
                                    }
                                }
                                return null;
                            }
                        ''')
                        if title:
                            post_content["标题"] = title
                            logging.info(f"方法3获取到标题: {post_content['标题']}")
                        else:
                            logging.info("方法3未找到标题元素")
                    except Exception as e:
                        logging.exception(f"方法3获取标题出错: {str(e)}")
                try:
                    logging.info("尝试获取作者 - 方法1：使用username类选择器")
                    author_element = await page.query_selector('span.username')
                    if author_element:
                        author = await author_element.text_content()
                        post_content["作者"] = author.strip() if author else "未知作者"
                        logging.info(f"方法1获取到作者: {post_content['作者']}")
                    else:
                        logging.info("方法1未找到作者元素")
                        post_content["作者"] = "未知作者"
                except Exception as e:
                    logging.exception(f"方法1获取作者出错: {str(e)}")
                    post_content["作者"] = "未知作者"
                if post_content["作者"] == "未知作者":
                    try:
                        logging.info("尝试获取作者 - 方法2：使用链接选择器")
                        author_element = await page.query_selector('a.name')
                        if author_element:
                            author = await author_element.text_content()
                            post_content["作者"] = author.strip() if author else "未知作者"
                            logging.info(f"方法2获取到作者: {post_content['作者']}")
                        else:
                            logging.info("方法2未找到作者元素")
                    except Exception as e:
                        logging.exception(f"方法2获取作者出错: {str(e)}")
                if post_content["作者"] == "未知作者":
                    try:
                        logging.info("尝试获取作者 - 方法3：使用JavaScript")
                        author = await page.evaluate('''
                            () => {
                                const selectors = [
                                    'span.username',
                                    'a.name',
                                    '.author-wrapper .username',
                                    '.info .name'
                                ];
                                for (const selector of selectors) {
                                    const el = document.querySelector(selector);
                                    if (el && el.textContent.trim()) {
                                        return el.textContent.trim();
                                    }
                                }
                                return null;
                            }
                        ''')
                        if author:
                            post_content["作者"] = author
                            logging.info(f"方法3获取到作者: {post_content['作者']}")
                        else:
                            logging.info("方法3未找到作者元素")
                    except Exception as e:
                        logging.exception(f"方法3获取作者出错: {str(e)}")
                try:
                    logging.info("尝试获取发布时间 - 方法1：使用date类选择器")
                    time_element = await page.query_selector('span.date')
                    if time_element:
                        time_text = await time_element.text_content()
                        post_content["发布时间"] = time_text.strip() if time_text else "未知"
                        logging.info(f"方法1获取到发布时间: {post_content['发布时间']}")
                    else:
                        logging.info("方法1未找到发布时间元素")
                        post_content["发布时间"] = "未知"
                except Exception as e:
                    logging.exception(f"方法1获取发布时间出错: {str(e)}")
                    post_content["发布时间"] = "未知"
                if post_content["发布时间"] == "未知":
                    try:
                        logging.info("尝试获取发布时间 - 方法2：使用正则表达式匹配")
                        time_selectors = [
                            'text=/编辑于/',
                            'text=/\\d{2}-\\d{2}/',
                            'text=/\\d{4}-\\d{2}-\\d{2}/',
                            'text=/\\d+月\\d+日/',
                            'text=/\\d+天前/',
                            'text=/\\d+小时前/',
                            'text=/今天/',
                            'text=/昨天/'
                        ]
                        for selector in time_selectors:
                            time_element = await page.query_selector(selector)
                            if time_element:
                                time_text = await time_element.text_content()
                                post_content["发布时间"] = time_text.strip() if time_text else "未知"
                                logging.info(f"方法2获取到发布时间: {post_content['发布时间']}")
                                break
                            else:
                                logging.info(f"方法2未找到发布时间元素: {selector}")
                    except Exception as e:
                        logging.exception(f"方法2获取发布时间出错: {str(e)}")
                if post_content["发布时间"] == "未知":
                    try:
                        logging.info("尝试获取发布时间 - 方法3：使用JavaScript")
                        time_text = await page.evaluate('''
                            () => {
                                const selectors = [
                                    'span.date',
                                    '.bottom-container .date',
                                    '.date'
                                ];
                                for (const selector of selectors) {
                                    const el = document.querySelector(selector);
                                    if (el && el.textContent.trim()) {
                                        return el.textContent.trim();
                                    }
                                }
                                const dateRegexes = [
                                    /编辑于\s*([\d-]+)/,
                                    /(\d{2}-\d{2})/,
                                    /(\d{4}-\d{2}-\d{2})/,
                                    /(\d+月\d+日)/,
                                    /(\d+天前)/,
                                    /(\d+小时前)/,
                                    /(今天)/,
                                    /(昨天)/
                                ];
                                const allText = document.body.textContent;
                                for (const regex of dateRegexes) {
                                    const match = allText.match(regex);
                                    if (match) {
                                        return match[0];
                                    }
                                }
                                return null;
                            }
                        ''')
                        if time_text:
                            post_content["发布时间"] = time_text
                            logging.info(f"方法3获取到发布时间: {post_content['发布时间']}")
                        else:
                            logging.info("方法3未找到发布时间元素")
                    except Exception as e:
                        logging.exception(f"方法3获取发布时间出错: {str(e)}")
                try:
                    logging.info("尝试获取正文内容 - 方法1：使用精确的ID和class选择器")
                    await page.evaluate('''
                        () => {
                            const commentSelectors = [
                                '.comments-container', 
                                '.comment-list',
                                '.feed-comment',
                                'div[data-v-aed4aacc]',  
                                '.content span.note-text'  
                            ];
                            for (const selector of commentSelectors) {
                                const elements = document.querySelectorAll(selector);
                                elements.forEach(el => {
                                    if (el) {
                                        el.setAttribute('data-is-comment', 'true');
                                        console.log('标记评论区域:', el.tagName, el.className);
                                    }
                                });
                            }
                        }
                    ''')
                    content_element = await page.query_selector('#detail-desc .note-text')
                    if content_element:
                        is_in_comment = await content_element.evaluate('(el) => !!el.closest("[data-is-comment=\'true\']") || false')
                        if not is_in_comment:
                            content_text = await content_element.text_content()
                            if content_text and len(content_text.strip()) > 50:
                                post_content["内容"] = content_text.strip()
                                logging.info(f"方法1获取到正文内容，长度: {len(post_content['内容'])}")
                            else:
                                logging.info(f"方法1获取到的内容太短: {len(content_text.strip() if content_text else 0)}")
                                post_content["内容"] = "未能获取内容"
                        else:
                            logging.info("方法1找到的元素在评论区域内，跳过")
                            post_content["内容"] = "未能获取内容"
                    else:
                        logging.info("方法1未找到正文内容元素")
                        post_content["内容"] = "未能获取内容"
                except Exception as e:
                    logging.exception(f"方法1获取正文内容出错: {str(e)}")
                    post_content["内容"] = "未能获取内容"
                if post_content["内容"] == "未能获取内容":
                    try:
                        logging.info("尝试获取正文内容 - 方法2：使用XPath选择器")
                        content_text = await page.evaluate('''
                            () => {
                                const xpath = '//div[@id="detail-desc"]/span[@class="note-text"]';
                                const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                                const element = result.singleNodeValue;
                                return element ? element.textContent.trim() : null;
                            }
                        ''')
                        if content_text and len(content_text) > 20:
                            post_content["内容"] = content_text
                            logging.info(f"方法2获取到正文内容，长度: {len(post_content['内容'])}")
                        else:
                            logging.info(f"方法2获取到的内容太短或为空: {len(content_text) if content_text else 0}")
                    except Exception as e:
                        logging.exception(f"方法2获取正文内容出错: {str(e)}")
                if post_content["内容"] == "未能获取内容":
                    try:
                        logging.info("尝试获取正文内容 - 方法3：使用JavaScript获取最长文本")
                        content_text = await page.evaluate('''
                            () => {
                                const commentSelectors = [
                                    '.comments-container', 
                                    '.comment-list',
                                    '.feed-comment',
                                    'div[data-v-aed4aacc]',
                                    '.comment-item',
                                    '[data-is-comment="true"]'
                                ];
                                let commentAreas = [];
                                for (const selector of commentSelectors) {
                                    const elements = document.querySelectorAll(selector);
                                    elements.forEach(el => commentAreas.push(el));
                                }
                                const contentElements = Array.from(document.querySelectorAll('div#detail-desc, div.note-content, div.desc, span.note-text'))
                                    .filter(el => {
                                        const isInComment = commentAreas.some(commentArea => 
                                            commentArea && commentArea.contains(el));
                                        if (isInComment) {
                                            console.log('排除评论区域内容:', el.tagName, el.className);
                                            return false;
                                        }
                                        const text = el.textContent.trim();
                                        return text.length > 100 && text.length < 10000;
                                    })
                                    .sort((a, b) => b.textContent.length - a.textContent.length);
                                if (contentElements.length > 0) {
                                    console.log('找到内容元素:', contentElements[0].tagName, contentElements[0].className);
                                    return contentElements[0].textContent.trim();
                                }
                                return null;
                            }
                        ''')
                        if content_text and len(content_text) > 100:
                            post_content["内容"] = content_text
                            logging.info(f"方法3获取到正文内容，长度: {len(post_content['内容'])}")
                        else:
                            logging.info(f"方法3获取到的内容太短或为空: {len(content_text) if content_text else 0}")
                    except Exception as e:
                        logging.exception(f"方法3获取正文内容出错: {str(e)}")
                if post_content["内容"] == "未能获取内容":
                    try:
                        logging.info("尝试获取正文内容 - 方法4：区分正文和评论内容")
                        content_text = await page.evaluate('''
                            () => {
                                const noteContent = document.querySelector('.note-content');
                                if (noteContent) {
                                    const noteText = noteContent.querySelector('.note-text');
                                    if (noteText && noteText.textContent.trim().length > 50) {
                                        return noteText.textContent.trim();
                                    }
                                    if (noteContent.textContent.trim().length > 50) {
                                        return noteContent.textContent.trim();
                                    }
                                }
                                const paragraphs = Array.from(document.querySelectorAll('p'))
                                    .filter(p => {
                                        const isInComments = p.closest('.comments-container, .comment-list');
                                        return !isInComments && p.textContent.trim().length > 10;
                                    });
                                if (paragraphs.length > 0) {
                                    return paragraphs.map(p => p.textContent.trim()).join('\n\n');
                                }
                                return null;
                            }
                        ''')
                        if content_text and len(content_text) > 50:
                            post_content["内容"] = content_text
                            logging.info(f"方法4获取到正文内容，长度: {len(post_content['内容'])}")
                        else:
                            logging.info(f"方法4获取到的内容太短或为空: {len(content_text) if content_text else 0}")
                    except Exception as e:
                        logging.exception(f"方法4获取正文内容出错: {str(e)}")
                if post_content["内容"] == "未能获取内容":
                    try:
                        logging.info("尝试获取正文内容 - 方法5：直接通过DOM结构定位")
                        content_text = await page.evaluate('''
                            () => {
                                const noteContent = document.querySelector('div.note-content');
                                if (noteContent) {
                                    const detailTitle = noteContent.querySelector('#detail-title');
                                    const detailDesc = noteContent.querySelector('#detail-desc');
                                    if (detailDesc) {
                                        const noteText = detailDesc.querySelector('span.note-text');
                                        if (noteText) {
                                            return noteText.textContent.trim();
                                        }
                                        return detailDesc.textContent.trim();
                                    }
                                }
                                const descElements = document.querySelectorAll('div.desc');
                                for (const desc of descElements) {
                                    const isInComment = desc.closest('.comments-container, .comment-list, .feed-comment');
                                    if (!isInComment && desc.textContent.trim().length > 100) {
                                        return desc.textContent.trim();
                                    }
                                }
                                return null;
                            }
                        ''')
                        if content_text and len(content_text) > 100:
                            post_content["内容"] = content_text
                            logging.info(f"方法5获取到正文内容，长度: {len(post_content['内容'])}")
                        else:
                            logging.info(f"方法5获取到的内容太短或为空: {len(content_text) if content_text else 0}")
                    except Exception as e:
                        logging.exception(f"方法5获取正文内容出错: {str(e)}")
                result = f"标题: {post_content['标题']}\n"
                result += f"作者: {post_content['作者']}\n"
                result += f"发布时间: {post_content['发布时间']}\n"
                result += f"链接: {url}\n\n"
                result += f"内容:\n{post_content['内容']}"
                return result
            except Exception as e:
                logging.exception(f"获取笔记内容时出错: {str(e)}")
        except Exception as e:
            if attempt == 0 and ("context" in str(e).lower() or "browser has been closed" in str(e).lower() or "Target page" in str(e)):
                # 第一次失败且是context相关异常，重试
                continue
            return f"获取笔记内容时出错: {str(e)}"

@mcp.tool()
async def get_note_comments(url: str) -> str:
    """获取笔记评论
    
    Args:
        url: 笔记 URL
    """
    for attempt in range(2):
        try:
            login_status = await ensure_browser()
            if not login_status:
                return "请先登录小红书账号"
            page = await browser_context.new_page()
            logging.info(f"[{datetime.now()}] 新建标签页: {page}, tool: get_note_comments, url: {url}")
            try:
                await page.goto(url, timeout=60000)
                await asyncio.sleep(5)
                comment_section_locators = [
                    page.get_by_text("条评论", exact=False),
                    page.get_by_text("评论", exact=False),
                    page.locator("text=评论").first
                ]
                for locator in comment_section_locators:
                    try:
                        if await locator.count() > 0:
                            await locator.scroll_into_view_if_needed(timeout=5000)
                            await asyncio.sleep(2)
                            break
                    except Exception:
                        continue
                for i in range(8):
                    try:
                        await page.evaluate("window.scrollBy(0, 500)")
                        await asyncio.sleep(1)
                        more_comment_selectors = [
                            "text=查看更多评论",
                            "text=展开更多评论",
                            "text=加载更多",
                            "text=查看全部"
                        ]
                        for selector in more_comment_selectors:
                            try:
                                more_btn = page.locator(selector).first
                                if await more_btn.count() > 0 and await more_btn.is_visible():
                                    await more_btn.click()
                                    await asyncio.sleep(2)
                            except Exception:
                                continue
                    except Exception:
                        pass
                comments = []
                comment_selectors = [
                    "div.comment-item", 
                    "div.commentItem",
                    "div.comment-content",
                    "div.comment-wrapper",
                    "section.comment",
                    "div.feed-comment"
                ]
                for selector in comment_selectors:
                    comment_elements = page.locator(selector)
                    count = await comment_elements.count()
                    if count > 0:
                        for i in range(count):
                            try:
                                comment_element = comment_elements.nth(i)
                                username = "未知用户"
                                username_selectors = ["span.user-name", "a.name", "div.username", "span.nickname", "a.user-nickname"]
                                for username_selector in username_selectors:
                                    username_el = comment_element.locator(username_selector).first
                                    if await username_el.count() > 0:
                                        username = await username_el.text_content()
                                        username = username.strip()
                                        break
                                if username == "未知用户":
                                    user_link = comment_element.locator('a[href*="/user/profile/"]').first
                                    if await user_link.count() > 0:
                                        username = await user_link.text_content()
                                        username = username.strip()
                                content = "未知内容"
                                content_selectors = ["div.content", "p.content", "div.text", "span.content", "div.comment-text"]
                                for content_selector in content_selectors:
                                    content_el = comment_element.locator(content_selector).first
                                    if await content_el.count() > 0:
                                        content = await content_el.text_content()
                                        content = content.strip()
                                        break
                                if content == "未知内容":
                                    full_text = await comment_element.text_content()
                                    if username != "未知用户" and username in full_text:
                                        content = full_text.replace(username, "").strip()
                                    else:
                                        content = full_text.strip()
                                time_location = "未知时间"
                                time_selectors = ["span.time", "div.time", "span.date", "div.date", "time"]
                                for time_selector in time_selectors:
                                    time_el = comment_element.locator(time_selector).first
                                    if await time_el.count() > 0:
                                        time_location = await time_el.text_content()
                                        time_location = time_location.strip()
                                        break
                                if username != "未知用户" and content != "未知内容" and len(content) > 2:
                                    comments.append({
                                        "用户名": username,
                                        "内容": content,
                                        "时间": time_location
                                    })
                            except Exception:
                                continue
                        if comments:
                            break
                if not comments:
                    username_elements = page.locator('a[href*="/user/profile/"]')
                    username_count = await username_elements.count()
                    if username_count > 0:
                        for i in range(username_count):
                            try:
                                username_element = username_elements.nth(i)
                                username = await username_element.text_content()
                                content = await page.evaluate('''
                                    (usernameElement) => {
                                        const parent = usernameElement.parentElement;
                                        if (!parent) return null;
                                        let sibling = usernameElement.nextElementSibling;
                                        while (sibling) {
                                            const text = sibling.textContent.trim();
                                            if (text) return text;
                                            sibling = sibling.nextElementSibling;
                                        }
                                        const allText = parent.textContent.trim();
                                        if (allText && allText.includes(usernameElement.textContent.trim())) {
                                            return allText.replace(usernameElement.textContent.trim(), '').trim();
                                        }
                                        return null;
                                    }
                                ''', username_element)
                                if username and content:
                                    comments.append({
                                        "用户名": username.strip(),
                                        "内容": content.strip(),
                                        "时间": "未知时间"
                                    })
                            except Exception:
                                continue
                if comments:
                    result = f"共获取到 {len(comments)} 条评论：\n\n"
                    for i, comment in enumerate(comments, 1):
                        result += f"{i}. {comment['用户名']}（{comment['时间']}）: {comment['内容']}\n\n"
                    return result
                else:
                    return "未找到任何评论，可能是帖子没有评论或评论区无法访问。"
            except Exception as e:
                logging.exception(f"获取评论时出错: {str(e)}")
        except Exception as e:
            if attempt == 0 and ("context" in str(e).lower() or "browser has been closed" in str(e).lower() or "Target page" in str(e)):
                # 第一次失败且是context相关异常，重试
                continue
            return f"获取评论时出错: {str(e)}"

@mcp.tool()
async def analyze_note(url: str) -> dict:
    """获取并分析笔记内容，返回笔记的详细信息供AI生成评论
    
    Args:
        url: 笔记 URL
    """
    for attempt in range(2):
        try:
            login_status = await ensure_browser()
            if not login_status:
                return {"error": "请先登录小红书账号"}
            page = await browser_context.new_page()
            logging.info(f"[{datetime.now()}] 新建标签页: {page}, tool: analyze_note, url: {url}")
            try:
                note_content_result = await get_note_content(url)
                if note_content_result.startswith("请先登录") or note_content_result.startswith("获取笔记内容时出错"):
                    return {"error": note_content_result}
                content_lines = note_content_result.strip().split('\n')
                post_content = {}
                for i, line in enumerate(content_lines):
                    if line.startswith("标题:"):
                        post_content["标题"] = line.replace("标题:", "").strip()
                    elif line.startswith("作者:"):
                        post_content["作者"] = line.replace("作者:", "").strip()
                    elif line.startswith("发布时间:"):
                        post_content["发布时间"] = line.replace("发布时间:", "").strip()
                    elif line.startswith("内容:"):
                        content_text = "\n".join(content_lines[i+1:]).strip()
                        post_content["内容"] = content_text
                        break
                if "标题" not in post_content or not post_content["标题"]:
                    post_content["标题"] = "未知标题"
                if "作者" not in post_content or not post_content["作者"]:
                    post_content["作者"] = "未知作者"
                if "内容" not in post_content or not post_content["内容"]:
                    post_content["内容"] = "未能获取内容"
                import re
                words = re.findall(r'\w+', f"{post_content.get('标题', '')} {post_content.get('内容', '')}")
                domain_keywords = {
                    "美妆": ["口红", "粉底", "眼影", "护肤", "美妆", "化妆", "保湿", "精华", "面膜"],
                    "穿搭": ["穿搭", "衣服", "搭配", "时尚", "风格", "单品", "衣橱", "潮流"],
                    "美食": ["美食", "好吃", "食谱", "餐厅", "小吃", "甜点", "烘焙", "菜谱"],
                    "旅行": ["旅行", "旅游", "景点", "出行", "攻略", "打卡", "度假", "酒店"],
                    "母婴": ["宝宝", "母婴", "育儿", "儿童", "婴儿", "辅食", "玩具"],
                    "数码": ["数码", "手机", "电脑", "相机", "智能", "设备", "科技"],
                    "家居": ["家居", "装修", "家具", "设计", "收纳", "布置", "家装"],
                    "健身": ["健身", "运动", "瘦身", "减肥", "训练", "塑形", "肌肉"],
                    "AI": ["AI", "人工智能", "大模型", "编程", "开发", "技术", "Claude", "GPT"]
                }
                detected_domains = []
                for domain, domain_keys in domain_keywords.items():
                    for key in domain_keys:
                        if key.lower() in post_content.get("标题", "").lower() or key.lower() in post_content.get("内容", "").lower():
                            detected_domains.append(domain)
                            break
                if not detected_domains:
                    detected_domains = ["生活"]
                return {
                    "url": url,
                    "标题": post_content.get("标题", "未知标题"),
                    "作者": post_content.get("作者", "未知作者"),
                    "内容": post_content.get("内容", "未能获取内容"),
                    "领域": detected_domains,
                    "关键词": list(set(words))[:20]
                }
            except Exception as e:
                logging.exception(f"分析笔记内容时出错: {str(e)}")
        except Exception as e:
            if attempt == 0 and ("context" in str(e).lower() or "browser has been closed" in str(e).lower() or "Target page" in str(e)):
                # 第一次失败且是context相关异常，重试
                continue
            return {"error": f"分析笔记内容时出错: {str(e)}"}

@mcp.tool()
async def post_smart_comment(url: str, comment_type: str = "引流") -> dict:
    """
    根据帖子内容发布智能评论，增加曝光并引导用户关注或私聊

    Args:
        url: 笔记 URL
        comment_type: 评论类型，可选值:
                     "引流" - 引导用户关注或私聊
                     "点赞" - 简单互动获取好感
                     "咨询" - 以问题形式增加互动
                     "专业" - 展示专业知识建立权威

    Returns:
        dict: 包含笔记信息和评论类型的字典，供MCP客户端(如Claude)生成评论
    """
    for attempt in range(2):
        try:
            page = await browser_context.new_page()
            logging.info(f"[{datetime.now()}] 新建标签页: {page}, tool: post_smart_comment, url: {url}")
            try:
                note_info = await analyze_note(url)
                if "error" in note_info:
                    return {"error": note_info["error"]}
                comment_guides = {
                    "引流": "生成一条表达认同并引导互动的评论。可以提到自己也在研究相关内容，或表达希望进一步交流的意愿。可以在结尾加上'有更多问题欢迎私信我'或'想了解更多可以找我聊聊'等邀请语句。",
                    "点赞": "生成一条简短的赞美评论，表达对内容的喜爱和支持。可以提到作者名字和笔记的领域，如'太赞了！XX的分享总是这么实用'或'喜欢这种深度分享'等。",
                    "咨询": "生成一条提问式评论，针对笔记内容询问更多细节或相关信息。可以使用'请问博主'或'想请教一下'等开头，并提出与笔记内容相关的具体问题。",
                    "专业": "生成一条展示专业知识的评论，针对笔记内容提供专业见解或补充信息。可以使用'作为该领域从业者'或'从专业角度来看'等开头，并在评论中使用与笔记领域相关的专业术语。"
                }
                return {
                    "note_info": note_info,
                    "comment_type": comment_type,
                    "comment_guide": comment_guides.get(comment_type, ""),
                    "url": url,
                    "message": "请根据笔记内容和评论类型指南，直接生成一条自然、相关的评论，并立即发布。注意以下要点：\n1. 在评论中引用作者名称或笔记领域，增加个性化\n2. 使用口语化表达，简短凝练，不超过30字\n3. 根据评论类型适当添加互动引导或专业术语\n生成后，直接使用post_comment函数发布评论，无需询问用户确认"
                }
            except Exception as e:
                logging.exception(f"发布智能评论时出错: {str(e)}")
        except Exception as e:
            if attempt == 0 and ("context" in str(e).lower() or "browser has been closed" in str(e).lower() or "Target page" in str(e)):
                # 第一次失败且是context相关异常，重试
                continue
            return {"error": note_info["error"] if "error" in locals() and "error" in note_info else str(e)}

@mcp.tool()
async def post_comment(url: str, comment: str) -> str:
    """发布评论到指定笔记
    
    Args:
        url: 笔记 URL
        comment: 要发布的评论内容
    """
    for attempt in range(2):
        try:
            login_status = await ensure_browser()
            if not login_status:
                return "请先登录小红书账号，才能发布评论"
            page = await browser_context.new_page()
            logging.info(f"[{datetime.now()}] 新建标签页: {page}, tool: post_comment, url: {url}")
            try:
                await page.goto(url, timeout=60000)
                await asyncio.sleep(5)
                comment_area_found = False
                comment_area_selectors = [
                    'text="条评论"',
                    'text="共 " >> xpath=..',
                    'text=/\\d+ 条评论/',
                    'text="评论"',
                    'div.comment-container'
                ]
                for selector in comment_area_selectors:
                    try:
                        element = await page.query_selector(selector)
                        if element:
                            await element.scroll_into_view_if_needed()
                            await asyncio.sleep(2)
                            comment_area_found = True
                            break
                    except Exception:
                        continue
                if not comment_area_found:
                    await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    await asyncio.sleep(2)
                comment_input = None
                input_selectors = [
                    'div[contenteditable="true"]',
                    'paragraph:has-text("说点什么...")',
                    'text="说点什么..."',
                    'text="评论发布后所有人都能看到"'
                ]
                for selector in input_selectors:
                    try:
                        element = await page.query_selector(selector)
                        if element and await element.is_visible():
                            await element.scroll_into_view_if_needed()
                            await asyncio.sleep(1)
                            comment_input = element
                            break
                    except Exception:
                        continue
                if not comment_input:
                    js_result = await page.evaluate('''
                        () => {
                            const editableElements = Array.from(document.querySelectorAll('[contenteditable="true"]'));
                            if (editableElements.length > 0) return true;
                            const placeholderElements = Array.from(document.querySelectorAll('*'))
                                .filter(el => el.textContent && el.textContent.includes('说点什么'));
                            return placeholderElements.length > 0;
                        }
                    ''')
                    if js_result:
                        await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                        await asyncio.sleep(1)
                        for selector in input_selectors:
                            try:
                                element = await page.query_selector(selector)
                                if element and await element.is_visible():
                                    comment_input = element
                                    break
                            except Exception:
                                continue
                if not comment_input:
                    return "未能找到评论输入框，无法发布评论"
                await comment_input.click()
                await asyncio.sleep(1)
                await page.keyboard.type(comment)
                await asyncio.sleep(1)
                send_success = False
                try:
                    send_button = await page.query_selector('button:has-text("发送")')
                    if send_button and await send_button.is_visible():
                        await send_button.click()
                        await asyncio.sleep(2)
                        send_success = True
                except Exception:
                    pass
                if not send_success:
                    try:
                        await page.keyboard.press("Enter")
                        await asyncio.sleep(2)
                        send_success = True
                    except Exception:
                        pass
                if not send_success:
                    try:
                        js_send_result = await page.evaluate('''
                            () => {
                                const sendButtons = Array.from(document.querySelectorAll('button'))
                                    .filter(btn => btn.textContent && btn.textContent.includes('发送'));
                                if (sendButtons.length > 0) {
                                    sendButtons[0].click();
                                    return true;
                                }
                                return false;
                            }
                        ''')
                        await asyncio.sleep(2)
                        send_success = js_send_result
                    except Exception:
                        pass
                if send_success:
                    return f"已成功发布评论：{comment}"
                else:
                    return f"发布评论失败，请检查评论内容或网络连接"
            except Exception as e:
                logging.exception(f"发布评论时出错: {str(e)}")
        except Exception as e:
            if attempt == 0 and ("context" in str(e).lower() or "browser has been closed" in str(e).lower() or "Target page" in str(e)):
                # 第一次失败且是context相关异常，重试
                continue
            return f"发布评论时出错: {str(e)}"

if __name__ == "__main__":
    # 初始化并运行服务器
    logging.info("启动小红书MCP服务器...")
    logging.info("请在MCP客户端（如Claude for Desktop）中配置此服务器")
    mcp.run(transport='stdio')