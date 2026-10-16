TAXONOMY_HTML = """
<html><body><ul>
  <li class="hot-jobs-first-item">
    <a class="hot-jobs-first-title">IT/互联网</a>
    <div class="hot-jobs-first-content">
      <div class="hot-jobs-second-item">
        <a class="hot-jobs-second-title" href="/career/542/">后端开发</a>
        <div class="hot-jobs-second-content">
          <a class="hot-jobs-three-title" href="https://www.liepin.com/career/java/" title="Java">Java</a>
          <a class="hot-jobs-three-title" href="/career/python/" title="Python">Python</a>
        </div>
      </div>
    </div>
  </li>
</ul></body></html>
"""


JOB_HTML = """
<html><head><title>Java招聘</title></head><body>
  <div class="job-list-box"><ul><li>
    <div class="job-card-pc-container">
      <div class="job-card-left-box">
        <a data-nick="job-detail-job-info"
           href="https://www.liepin.com/job/1984299681.shtml"
           data-jobId="84299681" data-jobKind="2">
          <div class="job-title-box">
            <div title="高级Java开发工程师">高级Java开发工程师</div>
            <div class="job-dq-box"><span class="ellipsis-1">武汉-中北路</span></div>
          </div>
          <span class="job-salary">18-29k·14薪</span>
          <div class="job-labels-box">
            <span class="labels-tag">5-8年</span>
            <span class="labels-tag">本科</span>
            <span class="labels-tag">五险一金</span>
          </div>
        </a>
        <div class="job-detail-company-box">
          <span class="company-name">示例科技有限公司</span>
          <div class="company-tags-box"><span>互联网</span><span>1000-2000人</span></div>
        </div>
      </div>
      <div class="job-card-right-box">
        <span class="recruiter-name">石女士</span><span>总部人事经理</span>
      </div>
    </div>
  </li></ul></div>
</body></html>
"""


EMPTY_JOB_HTML = """
<html><head><title>Python招聘</title></head><body>
  <div class="empty-content">非常抱歉！暂时没有合适的职位</div>
</body></html>
"""


DETAIL_JOB_HTML = """
<html><body>
  <dl class="paragraph">
    <dt>职位介绍</dt>
    <dd data-selector="job-intro-content">
      职位职责：<br><br>1、负责项目开发；<br>
      职位要求：<br>本科，3年以上经验。<br>
      技能要求：<br>熟悉 Python、SQL。
    </dd>
  </dl>
  <dl class="paragraph"><dt>其他信息</dt><dd>行业要求：全部行业</dd></dl>
</body></html>
"""


DETAIL_JOB_FALLBACK_HTML = """
<html><body><dl class="paragraph">
  <dt>职位介绍</dt><dd>职位职责：负责测试。职位要求：经验不限。</dd>
</dl></body></html>
"""


def build_job_page(
    jobs: list[tuple[int, str]],
    *,
    slug: str = "java",
    current_page: int = 0,
    page_links: tuple[int, ...] = (),
    next_slug: str | None = None,
) -> str:
    cards = []
    for job_id, salary in jobs:
        cards.append(
            f"""
            <div class="job-card-pc-container">
              <a data-nick="job-detail-job-info"
                 href="/job/{job_id}.shtml" data-jobId="{job_id}">
                <div class="job-title-box"><div title="岗位{job_id}">岗位{job_id}</div></div>
                <span class="job-salary">{salary}</span>
              </a>
              <span class="company-name">公司{job_id}</span>
            </div>
            """
        )
    links = []
    for page in page_links:
        link_slug = next_slug if page == current_page + 1 and next_slug else slug
        links.append(
            '<a data-selector="pagintion-item-selector" '
            f'href="/career/{link_slug}/pn{page}/">{page + 1}</a>'
        )
    pagination = (
        f'<nav class="pagination" data-currentpage="{current_page}">'
        f"{''.join(links)}</nav>"
        if page_links
        else ""
    )
    return (
        "<html><body><div class=\"job-list-box\">"
        + "".join(cards)
        + "</div>"
        + pagination
        + "</body></html>"
    )
