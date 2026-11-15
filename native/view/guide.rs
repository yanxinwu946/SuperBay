use gpui::{div, prelude::*, px};
use gpui_component::{
    ActiveTheme, Icon, IconName, Sizable, StyledExt, button::ButtonVariants, h_flex, v_flex,
};

use super::*;

impl Workspace {
    pub(super) fn open_guide(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        self.settings_page = None;
        self.guide = true;
        self.list_focus.focus(window);
        cx.notify();
    }

    pub(super) fn dismiss_guide(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        self.guide = false;
        if let Some(settings) = self.settings.as_mut() {
            let _ = settings.set_guide_seen(true);
        }
        if self.root.is_some() {
            self.focus_search(window, cx);
        }
        cx.notify();
    }

    pub(super) fn guide_content(&self, cx: &mut Context<Self>) -> gpui::AnyElement {
        let theme = cx.theme();
        let (line, muted, background) = (theme.border, theme.muted_foreground, theme.background);
        let mono = theme.mono_font_family.clone();
        v_flex()
            .size_full()
            .min_h_0()
            .bg(background)
            .child(
                h_flex()
                    .h(px(66.))
                    .flex_shrink_0()
                    .px_5()
                    .gap_3()
                    .border_b_1()
                    .border_color(line)
                    .child(
                        button("guide-back")
                            .ghost()
                            .small()
                            .icon(IconName::ArrowLeft)
                            .label("返回工作台")
                            .on_click(cx.listener(|this, _, window, cx| {
                                this.dismiss_guide(window, cx)
                            })),
                    )
                    .child(div().text_size(px(19.)).font_semibold().child("开始使用")),
            )
            .child(
                div()
                    .id("guide-scroll")
                    .flex_1()
                    .min_h_0()
                    .overflow_y_scroll()
                    .px_6()
                    .py_8()
                    .child(
                        v_flex()
                            .w_full()
                            .max_w(px(800.))
                            .mx_auto()
                            .gap_6()
                            .child(
                                h_flex()
                                    .gap_4()
                                    .items_start()
                                    .child(
                                        gpui::img(crate::LOGO.clone())
                                            .size(px(56.))
                                            .flex_shrink_0(),
                                    )
                                    .child(
                                        v_flex()
                                            .gap_2()
                                            .child(
                                                div()
                                                    .text_size(px(26.))
                                                    .font_semibold()
                                                    .child("先规划文件夹，再开始使用"),
                                            )
                                            .child(
                                                div()
                                                    .text_sm()
                                                    .text_color(muted)
                                                    .child("SuperBay 只读取目录，不移动文件。选一个存放工具的根目录，按分类整理；已有目录可以直接使用。"),
                                            ),
                                    ),
                            )
                            .child(
                                h_flex()
                                    .gap_4()
                                    .child(
                                        v_flex()
                                            .flex_1()
                                            .gap_4()
                                            .child(guide_card(
                                                IconName::FolderOpen,
                                                "先选工作区",
                                                "工作区是存放分类的那一层，例如 D:\\Tools。不要直接选到某个工具内部。",
                                                theme,
                                            ))
                                            .child(guide_card(
                                                IconName::FolderClosed,
                                                "一个工具一个文件夹",
                                                "保留工具原有目录，脚本、配置和输出都放在里面，扫描时不会拆散。",
                                                theme,
                                            )),
                                    )
                                    .child(
                                        v_flex()
                                            .flex_1()
                                            .gap_4()
                                            .child(guide_card(
                                                IconName::BookOpen,
                                                "分类用 A-01-名称",
                                                "字母和编号让排序稳定，名称按自己的领域来写。也可以只扫描这种命名的目录。",
                                                theme,
                                            ))
                                            .child(guide_card(
                                                IconName::Search,
                                                "自动扫描",
                                                "选好工作区后会自动识别分类和工具；新增或移动目录也会自动更新，不需要手动设置层级。",
                                                theme,
                                            )),
                                    ),
                            )
                            .child(
                                v_flex()
                                    .gap_3()
                                    .p_5()
                                    .rounded_lg()
                                    .border_1()
                                    .border_color(line)
                                    .child(
                                        div()
                                            .text_sm()
                                            .font_semibold()
                                            .child("可以直接照着放"),
                                    )
                                    .child(
                                        v_flex()
                                            .gap_1()
                                            .p_4()
                                            .rounded_md()
                                            .bg(theme.muted)
                                            .font_family(mono)
                                            .text_sm()
                                            .children(
                                                [
                                                    "D:\\Tools\\",
                                                    "├── A-01-图像处理\\",
                                                    "│   ├── ImageTool\\",
                                                    "│   │   └── ImageTool.exe",
                                                    "│   └── 批处理脚本\\",
                                                    "│       └── convert.py",
                                                    "├── B-02-视频处理\\",
                                                    "│   └── VideoTool\\",
                                                    "└── C-03-资料\\",
                                                    "    └── 配色参考\\",
                                                ]
                                                .map(|line| div().whitespace_nowrap().child(line)),
                                            ),
                                    )
                                    .child(
                                        div().text_xs().text_color(muted).child(
                                            "工作区根目录、分类、工具各就各位；工具内部的文件不用改。",
                                        ),
                                    ),
                            )
                            .child(
                                v_flex()
                                    .gap_3()
                                    .p_5()
                                    .rounded_lg()
                                    .border_1()
                                    .border_color(line)
                                    .child(
                                        div()
                                            .text_sm()
                                            .font_semibold()
                                            .child("已有目录也能直接用"),
                                    )
                                    .child(
                                        div().text_xs().text_color(muted).child(
                                            "不需要移动文件。选择后会自动扫描，之后可以在「设置 → 工作区」里继续调整。",
                                        ),
                                    )
                                    .child(
                                        h_flex()
                                            .gap_2()
                                            .child(
                                                button("guide-pick")
                                                    .primary()
                                                    .label("选择工作区目录")
                                                    .on_click(cx.listener(
                                                        |this, _, window, cx| {
                                                            this.pick_workspace(window, cx)
                                                        },
                                                    )),
                                            )
                                            .child(
                                                button("guide-later")
                                                    .ghost()
                                                    .label("进入工作台")
                                                    .on_click(cx.listener(
                                                        |this, _, window, cx| {
                                                            this.dismiss_guide(window, cx)
                                                        },
                                                    )),
                                            ),
                                    ),
                            ),
                    ),
            )
            .into_any_element()
    }
}

fn guide_card(
    icon: IconName,
    title: &'static str,
    body: &'static str,
    theme: &gpui_component::Theme,
) -> impl IntoElement {
    v_flex()
        .gap_2()
        .p_4()
        .rounded_lg()
        .border_1()
        .border_color(theme.border)
        .child(
            div()
                .p_2()
                .rounded_md()
                .bg(theme.accent)
                .child(Icon::new(icon).small().text_color(theme.primary)),
        )
        .child(div().text_sm().font_semibold().child(title))
        .child(
            div()
                .text_xs()
                .text_color(theme.muted_foreground)
                .child(body),
        )
}
