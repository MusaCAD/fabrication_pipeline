// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright (C) 2026 Pranay Kiran
// musa_plot — project-local headless .musa -> PDF plot harness for the fabrication
// pipeline. Derived from musa_cad/tools/plot_check.cpp (LGPL-3.0-or-later), extended
// with CLI control over paper size, orientation, fixed scale, window, DPI and style.
// Links against the read-only MusaCAD release build; the musa_cad tree is not modified.
//
//   musa_plot --file <in.musa|in.dxf> <out.pdf> [options]
//     --paper A4|A3|A2|A1|A0|<W>x<H>   paper size in mm (default A4)
//     --portrait | --landscape          orientation (default portrait)
//     --scale fit | <N>:<D>             fit-to-paper, or N paper-mm per D drawing units
//     --window minx,miny,maxx,maxy      world window (default: drawing extents)
//     --dpi <n>                         device resolution (default 300)
//     --style none|mono|gray            colour mapping (default none)
//     --no-center                       anchor top-left instead of centring
//     --offset <x>,<y>                  extra offset in paper mm
//     --no-lineweights                  cosmetic hairlines instead of real widths
//     --stamp <png>,<x>,<y>,<w>,<h>     draw a raster image (shaded pictorial)
//                                       at the given rect in WORLD/sheet units
//                                       (x,y = lower-left), repeatable
//
// Prints one "[musa_plot] ..." summary line with the EFFECTIVE mm-per-unit and paper
// geometry so the pipeline can record/verify the true plotted scale.
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include <QGuiApplication>
#include <QImage>
#include <QPageLayout>
#include <QPageSize>
#include <QPainter>
#include <QPdfWriter>
#include <QSizeF>

#include "musacad/core/block_resolve.hpp"
#include "musacad/core/io/document.hpp"
#include "musacad/core/io/dxf.hpp"
#include "musacad/core/io/native_format.hpp"
#include "musacad/core/native_kernel_2d.hpp"
#include "musacad/core/scene_snapshot.hpp"
#include "musacad/ui/plot.hpp"

using namespace musacad;

namespace {

struct Stamp {
    std::string path;
    double x = 0, y = 0, w = 0, h = 0;   // world/sheet units, lower-left
};

struct Options {
    std::string in_path;
    std::string out_path;
    std::vector<Stamp> stamps;
    double paper_w = 210.0; // portrait A4 default (w < h)
    double paper_h = 297.0;
    std::string paper_name = "A4";
    bool landscape = false;
    bool fit = true;
    double scale_num = 1.0;
    double scale_den = 1.0;
    bool have_window = false;
    double win[4] = {0, 0, 0, 0};
    int dpi = 300;
    ui::PlotSpec::Style style = ui::PlotSpec::Style::None;
    bool center = true;
    double off_x = 0.0, off_y = 0.0;
    bool lineweights = true;
    double min_lw_mm = 0.0;   // floor for hairline strokes (stroke-font
                              // text plots at 0 width upstream)
};

// Local copy of ui::paint_plot (musa_cad/src/ui/plot.cpp, LGPL-3.0) with one
// extension: raster stamps drawn in the SAME painter session (a second
// QPainter on a QPdfWriter would open a new page). Kept byte-faithful in the
// geometry path; only the stamp block at the end is new.
void paint_plot_stamped(QPaintDevice& device, const musacad::core::RenderSnapshot& snap,
                        const ui::PlotSpec& spec, core::Vec2 amin, core::Vec2 amax,
                        const std::vector<Stamp>& stamps, double min_lw_mm = 0.0) {
    constexpr double kMmPerInch = 25.4;
    const double dev_w = device.width();
    const double dev_h = device.height();
    const double dpx = device.logicalDpiX() > 0 ? device.logicalDpiX() : 96.0;
    const double dpy = device.logicalDpiY() > 0 ? device.logicalDpiY() : 96.0;
    if (dev_w <= 0.0 || dev_h <= 0.0) {
        return;
    }
    double aw = amax.x - amin.x;
    double ah = amax.y - amin.y;
    if (!(aw > 0.0)) aw = 1.0;
    if (!(ah > 0.0)) ah = 1.0;
    double mm_per_unit = 0.0;
    if (spec.fit) {
        const double paper_w_mm = dev_w / dpx * kMmPerInch;
        const double paper_h_mm = dev_h / dpy * kMmPerInch;
        mm_per_unit = std::min(paper_w_mm / aw, paper_h_mm / ah);
    } else {
        mm_per_unit = spec.scale_den != 0.0 ? spec.scale_num / spec.scale_den : 1.0;
    }
    if (!(mm_per_unit > 0.0)) mm_per_unit = 1.0;
    const double px_per_unit_x = mm_per_unit * dpx / kMmPerInch;
    const double px_per_unit_y = mm_per_unit * dpy / kMmPerInch;
    const double scaled_w = aw * px_per_unit_x;
    const double scaled_h = ah * px_per_unit_y;
    const double off_x_px = spec.off_x_mm * dpx / kMmPerInch;
    const double off_y_px = spec.off_y_mm * dpy / kMmPerInch;
    const double ox = (spec.center ? (dev_w - scaled_w) * 0.5 : 0.0) + off_x_px;
    const double oy = (spec.center ? (dev_h - scaled_h) * 0.5 : 0.0) + off_y_px;
    const auto to_dev = [&](const core::Vec2& w) -> QPointF {
        const double x = ox + (w.x - amin.x) * px_per_unit_x;
        const double y = oy + (ah - (w.y - amin.y)) * px_per_unit_y;
        return {x, y};
    };

    QPainter p(&device);
    p.setRenderHint(QPainter::Antialiasing, true);
    p.setRenderHint(QPainter::SmoothPixmapTransform, true);
    p.fillRect(0, 0, static_cast<int>(dev_w), static_cast<int>(dev_h), Qt::white);
    {
        const QPointF tl = to_dev({amin.x, amax.y});
        const QPointF br = to_dev({amax.x, amin.y});
        p.setClipRect(QRectF(tl, br).normalized());
    }
    QPolygonF tri(3);
    for (const core::ColorBatch& b : snap.fill_batches) {
        const core::Rgb c = ui::plot_color(b.color, spec.style);
        p.setPen(Qt::NoPen);
        p.setBrush(QBrush(QColor(c.r, c.g, c.b)));
        for (std::uint32_t i = 0; i + 2 < b.count; i += 3) {
            const std::uint32_t base = b.first + i;
            tri[0] = to_dev(snap.fill_vertices[base]);
            tri[1] = to_dev(snap.fill_vertices[base + 1]);
            tri[2] = to_dev(snap.fill_vertices[base + 2]);
            p.drawPolygon(tri);
        }
    }
    p.setBrush(Qt::NoBrush);
    for (const core::ColorBatch& b : snap.line_batches) {
        const core::Rgb c = ui::plot_color(b.color, spec.style);
        double width_px = 0.0;
        if (spec.plot_lineweights && snap.lineweight_display && b.lineweight > 0) {
            width_px = (static_cast<double>(b.lineweight) / 100.0) * dpx / kMmPerInch;
        }
        // upstream renders stroke-font text at width 0 regardless of the
        // entity lineweight; floor hairlines so printed text stays legible
        if (min_lw_mm > 0.0 && width_px <= 0.0) {
            width_px = min_lw_mm * dpx / kMmPerInch;
        }
        QPen pen(QColor(c.r, c.g, c.b));
        pen.setWidthF(width_px);
        pen.setCapStyle(Qt::RoundCap);
        pen.setJoinStyle(Qt::RoundJoin);
        p.setPen(pen);
        core::for_each_line_segment(snap, b, [&](const core::Vec2& a, const core::Vec2& c2) {
            p.drawLine(QLineF(to_dev(a), to_dev(c2)));
        });
    }
    for (const core::ColorBatch& b : snap.point_batches) {
        const core::Rgb c = ui::plot_color(b.color, spec.style);
        p.setPen(Qt::NoPen);
        p.setBrush(QBrush(QColor(c.r, c.g, c.b)));
        const double r = std::max(0.5, 0.3 * dpx / kMmPerInch);
        for (std::uint32_t i = 0; i < b.count; ++i) {
            p.drawEllipse(to_dev(snap.points[b.first + i]), r, r);
        }
    }
    // --- extension: raster stamps (shaded pictorials), world-unit rects ---
    for (const Stamp& st : stamps) {
        QImage img(QString::fromStdString(st.path));
        if (img.isNull()) {
            std::printf("[musa_plot] stamp SKIPPED (unreadable): %s\n", st.path.c_str());
            continue;
        }
        const QPointF tl = to_dev({st.x, st.y + st.h});
        const QPointF br = to_dev({st.x + st.w, st.y});
        p.drawImage(QRectF(tl, br).normalized(), img);
    }
    p.end();
}

void usage() {
    std::fprintf(stderr,
                 "usage: musa_plot --file <in.musa|in.dxf> <out.pdf>\n"
                 "  [--paper A4|A3|A2|A1|A0|<W>x<H>] [--portrait|--landscape]\n"
                 "  [--scale fit|<N>:<D>] [--window minx,miny,maxx,maxy] [--dpi <n>]\n"
                 "  [--style none|mono|gray] [--no-center] [--offset x,y] [--no-lineweights]\n");
}

bool parse_paper(const std::string& s, Options& o) {
    // ISO A-series, portrait W x H in mm.
    static const struct { const char* name; double w, h; } kSizes[] = {
        {"A4", 210.0, 297.0}, {"A3", 297.0, 420.0}, {"A2", 420.0, 594.0},
        {"A1", 594.0, 841.0}, {"A0", 841.0, 1189.0},
    };
    for (const auto& k : kSizes) {
        if (s == k.name) {
            o.paper_w = k.w;
            o.paper_h = k.h;
            o.paper_name = k.name;
            return true;
        }
    }
    double w = 0, h = 0;
    if (std::sscanf(s.c_str(), "%lfx%lf", &w, &h) == 2 && w > 0 && h > 0) {
        o.paper_w = w;
        o.paper_h = h;
        o.paper_name = "custom";
        return true;
    }
    return false;
}

bool parse_args(int argc, char** argv, Options& o) {
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        auto next = [&](const char* flag) -> const char* {
            if (i + 1 >= argc) {
                std::fprintf(stderr, "[musa_plot] %s needs a value\n", flag);
                return nullptr;
            }
            return argv[++i];
        };
        if (a == "--file") {
            const char* v = next("--file");
            if (!v) return false;
            o.in_path = v;
            if (i + 1 < argc && argv[i + 1][0] != '-') o.out_path = argv[++i];
        } else if (a == "--paper") {
            const char* v = next("--paper");
            if (!v || !parse_paper(v, o)) return false;
        } else if (a == "--portrait") {
            o.landscape = false;
        } else if (a == "--landscape") {
            o.landscape = true;
        } else if (a == "--scale") {
            const char* v = next("--scale");
            if (!v) return false;
            if (std::strcmp(v, "fit") == 0) {
                o.fit = true;
            } else {
                double n = 0, d = 0;
                if (std::sscanf(v, "%lf:%lf", &n, &d) != 2 || n <= 0 || d <= 0) {
                    std::fprintf(stderr, "[musa_plot] bad --scale '%s' (want fit or N:D)\n", v);
                    return false;
                }
                o.fit = false;
                o.scale_num = n;
                o.scale_den = d;
            }
        } else if (a == "--window") {
            const char* v = next("--window");
            if (!v) return false;
            if (std::sscanf(v, "%lf,%lf,%lf,%lf", &o.win[0], &o.win[1], &o.win[2], &o.win[3]) != 4) {
                std::fprintf(stderr, "[musa_plot] bad --window '%s'\n", v);
                return false;
            }
            o.have_window = true;
        } else if (a == "--dpi") {
            const char* v = next("--dpi");
            if (!v) return false;
            o.dpi = std::atoi(v);
            if (o.dpi < 72 || o.dpi > 2400) {
                std::fprintf(stderr, "[musa_plot] --dpi out of range\n");
                return false;
            }
        } else if (a == "--style") {
            const char* v = next("--style");
            if (!v) return false;
            if (std::strcmp(v, "none") == 0) o.style = ui::PlotSpec::Style::None;
            else if (std::strcmp(v, "mono") == 0) o.style = ui::PlotSpec::Style::Monochrome;
            else if (std::strcmp(v, "gray") == 0) o.style = ui::PlotSpec::Style::Grayscale;
            else { std::fprintf(stderr, "[musa_plot] bad --style '%s'\n", v); return false; }
        } else if (a == "--no-center") {
            o.center = false;
        } else if (a == "--offset") {
            const char* v = next("--offset");
            if (!v) return false;
            if (std::sscanf(v, "%lf,%lf", &o.off_x, &o.off_y) != 2) return false;
        } else if (a == "--no-lineweights") {
            o.lineweights = false;
        } else if (a == "--min-lw") {
            const char* v = next("--min-lw");
            if (!v) return false;
            o.min_lw_mm = std::atof(v);
        } else if (a == "--stamp") {
            const char* v = next("--stamp");
            if (!v) return false;
            const std::string s(v);
            const auto comma = s.find(',');
            Stamp st;
            if (comma == std::string::npos ||
                std::sscanf(s.c_str() + comma + 1, "%lf,%lf,%lf,%lf",
                            &st.x, &st.y, &st.w, &st.h) != 4 ||
                st.w <= 0 || st.h <= 0) {
                std::fprintf(stderr, "[musa_plot] bad --stamp '%s'\n", v);
                return false;
            }
            st.path = s.substr(0, comma);
            o.stamps.push_back(st);
        } else {
            std::fprintf(stderr, "[musa_plot] unknown argument '%s'\n", a.c_str());
            return false;
        }
    }
    return !o.in_path.empty() && !o.out_path.empty();
}

} // namespace

int main(int argc, char** argv) {
    QGuiApplication app(argc, argv);

    Options o;
    if (!parse_args(argc, argv, o)) {
        usage();
        return 2;
    }

    std::ifstream in(o.in_path, std::ios::binary);
    if (!in) {
        std::printf("[musa_plot] cannot open %s\n", o.in_path.c_str());
        return 1;
    }
    std::stringstream ss;
    ss << in.rdbuf();
    const std::string text = ss.str();

    core::io::Document doc;
    const bool is_dxf =
        o.in_path.size() > 4 && o.in_path.substr(o.in_path.size() - 4) == ".dxf";
    const core::io::IoResult r =
        is_dxf ? core::io::parse_dxf(text, doc) : core::io::parse_native(text, doc);
    if (!r.ok) {
        std::printf("[musa_plot] parse FAILED: %s\n", r.message.c_str());
        return 1;
    }
    core::GeometryStore store;
    core::io::populate_store(store, doc);

    // Area: explicit window, else extents from a probe snapshot (same as plot_check).
    core::Vec2 mn{1e300, 1e300};
    core::Vec2 mx{-1e300, -1e300};
    if (o.have_window) {
        mn = {o.win[0], o.win[1]};
        mx = {o.win[2], o.win[3]};
    } else {
        core::NativeKernel2D kernel;
        core::RenderSnapshot probe;
        core::build_render_snapshot(store, kernel, probe, 0.5, store.ltscale());
        for (const core::Vec2& v : probe.line_vertices) {
            mn = {std::min(mn.x, v.x), std::min(mn.y, v.y)};
            mx = {std::max(mx.x, v.x), std::max(mx.y, v.y)};
        }
        for (const core::Vec2& v : probe.fill_vertices) {
            mn = {std::min(mn.x, v.x), std::min(mn.y, v.y)};
            mx = {std::max(mx.x, v.x), std::max(mx.y, v.y)};
        }
        if (mn.x > mx.x || mn.y > mx.y) {
            std::printf("[musa_plot] empty drawing, nothing to plot\n");
            return 1;
        }
    }

    ui::PlotSpec spec;
    spec.paper_w_mm = o.landscape ? std::max(o.paper_w, o.paper_h) : std::min(o.paper_w, o.paper_h);
    spec.paper_h_mm = o.landscape ? std::min(o.paper_w, o.paper_h) : std::max(o.paper_w, o.paper_h);
    spec.paper = o.paper_name;
    spec.landscape = o.landscape;
    spec.area = ui::PlotSpec::Area::Window;
    spec.win_min = mn;
    spec.win_max = mx;
    spec.fit = o.fit;
    spec.scale_num = o.scale_num;
    spec.scale_den = o.scale_den;
    spec.center = o.center;
    spec.off_x_mm = o.off_x;
    spec.off_y_mm = o.off_y;
    spec.plot_lineweights = o.lineweights;
    spec.style = o.style;

    // Tessellation tolerance: plotted area vs paper diagonal at device DPI
    // (the MainWindow::prepare_plot rule, with the real paper instead of A4).
    const double area_diag = std::max(core::length(mx - mn), 1e-9);
    const double paper_diag_px =
        std::hypot(spec.paper_w_mm, spec.paper_h_mm) / 25.4 * o.dpi;
    const double tol = std::max(area_diag / paper_diag_px * 0.3, 1e-9);

    core::NativeKernel2D kernel;
    core::RenderSnapshot snap;
    core::build_render_snapshot(store, kernel, snap, tol, store.ltscale());

    QPdfWriter w(QString::fromStdString(o.out_path));
    w.setPageSize(QPageSize(QSizeF(spec.paper_w_mm, spec.paper_h_mm), QPageSize::Millimeter));
    w.setResolution(o.dpi);

    // Effective world->paper scale, mirroring paint_plot's arithmetic on the real device.
    const double paintable_w_mm = double(w.width()) / w.logicalDpiX() * 25.4;
    const double paintable_h_mm = double(w.height()) / w.logicalDpiY() * 25.4;
    const double aw = mx.x - mn.x;
    const double ah = mx.y - mn.y;
    const double mm_per_unit =
        spec.fit ? std::min(paintable_w_mm / aw, paintable_h_mm / ah)
                 : spec.scale_num / spec.scale_den;

    if (o.stamps.empty() && o.min_lw_mm <= 0.0) {
        ui::paint_plot(w, snap, spec, mn, mx);
    } else {
        paint_plot_stamped(w, snap, spec, mn, mx, o.stamps, o.min_lw_mm);
    }

    char scale_str[64];
    if (o.fit)
        std::snprintf(scale_str, sizeof scale_str, "fit");
    else
        std::snprintf(scale_str, sizeof scale_str, "%g:%g", o.scale_num, o.scale_den);
    std::printf("[musa_plot] ok paper=%s %s %.0fx%.0fmm paintable=%.2fx%.2fmm dpi=%d "
                "window=(%.3f,%.3f)..(%.3f,%.3f) mm_per_unit=%.6f scale=%s lines=%zu fills=%zu -> %s\n",
                spec.paper.c_str(), o.landscape ? "landscape" : "portrait", spec.paper_w_mm,
                spec.paper_h_mm, paintable_w_mm, paintable_h_mm, o.dpi, mn.x, mn.y, mx.x, mx.y,
                mm_per_unit, scale_str,
                snap.line_vertices.size(), snap.fill_vertices.size(), o.out_path.c_str());
    return 0;
}
