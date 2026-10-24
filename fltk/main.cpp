// SPDX-License-Identifier: MIT
#include "appearance.h"
#include "desktop-menu.h"
#include "gpu-window.h"
#include <FL/Fl_Button.H>
#include <FL/Fl_Check_Button.H>
#include <FL/Fl_Choice.H>
#include <FL/Fl_Double_Window.H>
#include <FL/Fl_Input.H>
#include <FL/Fl_Int_Input.H>
#include <FL/Fl_Menu_Bar.H>
#include <FL/Fl_Multiline_Output.H>
#include <FL/Fl_Native_File_Chooser.H>
#include <FL/Fl_Progress.H>
#include <FL/Fl_Return_Button.H>
#include <FL/Fl_Scroll.H>
#include <FL/Fl_Value_Input.H>
#include <FL/fl_ask.H>
#include <chrono>
#include <fcntl.h>
#include <glib.h>
#include <iostream>
#include <signal.h>
namespace fs = std::filesystem;
class Converter : public dx::GpuWindow {
  DesktopMenuBar menu{0, 0, 800, 25};
  std::unique_ptr<DesktopMenu> desktop;
  Json preferences;
  std::string quality = "full",
              vaapi = "/dev/dri/by-path/pci-0000:03:00.0-render",
              overrideDevice;
  int frameLimit = 0;
  Fl_Scroll form{0, 25, 800, 395};
  Fl_Input source{150, 30, 540, 30, "Input video"},
      output{150, 80, 540, 30, "Output video"},
      model{150, 130, 540, 30, "Depth model"};
  Fl_Button browseIn{705, 30, 80, 30, "Browse"},
      browseOut{705, 80, 80, 30, "Browse"},
      browseModel{705, 130, 80, 30, "Browse"};
  Fl_Choice device{150, 180, 220, 30, "Compute"},
      layout{520, 180, 220, 30, "Layout"},
      encoder{150, 230, 220, 30, "Encoder"};
  Fl_Value_Input strength{520, 230, 100, 30, "Depth strength"},
      convergence{520, 280, 100, 30, "Convergence"};
  Fl_Check_Button swap{150, 280, 220, 30, "Swap eyes"},
      overwrite{150, 330, 250, 30, "Replace existing output"};
  Fl_Group footer{0, 420, 800, 180};
  Fl_Button start{150, 380, 220, 32, "Convert"};
  Fl_Button cancel{390, 380, 150, 32, "Cancel"},
      play{550, 380, 220, 32, "Play output"};
  Fl_Progress progress{20, 435, 760, 25};
  Fl_Multiline_Output log{20, 475, 760, 105};
  pid_t child = -1;
  int fd = -1;
  fs::path jobfile;
  std::string buffer, messages;
  bool stopping = false;
  uint64_t appearanceRevision = UINT64_MAX;
  std::string completedOutput, completedLayout;
  bool completedSwap = false;
  std::string pendingOutput, pendingLayout;
  bool pendingSwap = false;
  std::chrono::steady_clock::time_point stopped;
  static void tick(void *p) {
    auto self = static_cast<Converter *>(p);
    self->pump();
    Fl::repeat_timeout(.1, tick, p);
  }
  void showlog(std::string s) {
    messages += s + "\n";
    if (messages.size() > 24000)
      messages.erase(0, messages.size() - 20000);
    log.value(messages.c_str());
  }
  void choose(Fl_Input &field, int type) {
    Fl_Native_File_Chooser c;
    c.type(type);
    if (c.show() == 0) {
      if (&field == &source)
        setSource(c.filename());
      else
        field.value(c.filename());
    }
  }
  void cleanup() {
    if (fd >= 0) {
      close(fd);
      fd = -1;
    }
    if (!jobfile.empty()) {
      std::error_code ec;
      fs::remove(jobfile, ec);
      jobfile.clear();
    }
    form.activate();
    start.activate();
    cancel.deactivate();
    child = -1;
  }
  void event(const Json &j) {
    auto kind = j.value("event", std::string());
    if (kind == "failed")
      showlog(j.value("message", std::string("Conversion failed")));
    else if (kind == "completed") {
      auto result = j.value("result", Json::object());
      completedOutput = result.value("output", pendingOutput);
      completedLayout = result.value("layout", pendingLayout);
      completedSwap =
          result.value("eye_order", pendingSwap ? std::string("right_left")
                                                : std::string("left_right")) ==
          "right_left";
      showlog("Completed: " + completedOutput);
      progress.value(1);
    } else if (kind == "cancelled")
      showlog("Cancelled");
    else if (kind == "progress") {
      auto phase = j.value("phase", std::string());
      int frame = j.value("frames", 0), total = j.value("total", 0);
      if (total > 0)
        progress.value(double(frame) / total);
      showlog((phase.empty() ? std::string("Converting") : phase) + ": " +
              std::to_string(frame) +
              (total > 0 ? " / " + std::to_string(total) : " frames"));
    }
  }
  void pump() {
    if (appearanceRevision != dx::font_revision) {
      appearanceRevision = dx::font_revision;
      relayout();
    }
    bool ready =
        !completedOutput.empty() && fs::is_regular_file(completedOutput);
    if (ready)
      play.activate();
    else
      play.deactivate();
    dx::enable_menu(menu, "File/Play output", ready);
    dx::enable_menu(menu, "Conversion/Convert", child <= 0);
    dx::enable_menu(menu, "Conversion/Cancel", child > 0);
    dx::enable_menu(menu, "Conversion/More options", child <= 0);
    for (auto name :
         {"File/Open video", "File/Output path", "File/Depth model"})
      dx::enable_menu(menu, name, child <= 0);

    if (child <= 0)
      return;
    char data[8192];
    for (;;) {
      auto n = read(fd, data, sizeof(data));
      if (n <= 0)
        break;
      buffer.append(data, n);
      while (buffer.find('\n') != std::string::npos) {
        auto pos = buffer.find('\n');
        auto line = buffer.substr(0, pos);
        buffer.erase(0, pos + 1);
        try {
          event(Json::parse(line));
        } catch (...) {
          showlog(line);
        }
      }
    }
    int status = 0;
    if (waitpid(child, &status, WNOHANG) == child) {
      showlog(WIFEXITED(status)
                  ? "Worker exited: " + std::to_string(WEXITSTATUS(status))
                  : "Worker terminated");
      cleanup();
    } else if (stopping && std::chrono::steady_clock::now() - stopped >
                               std::chrono::seconds(5)) {
      kill(-child, SIGKILL);
      kill(child, SIGKILL);
    }
  }
  void begin() {
    if (child > 0)
      return;
    try {
      fs::path in = fs::weakly_canonical(fs::absolute(source.value())),
               out = fs::weakly_canonical(fs::absolute(output.value())),
               m = fs::weakly_canonical(fs::absolute(model.value()));
      if (!*source.value() || !fs::is_regular_file(in) || !*output.value() ||
          !fs::is_regular_file(m))
        throw std::runtime_error(
            "Choose an existing input and model, and an output path");
      if (out == in || out == m || out == m.replace_extension(".bin"))
        throw std::runtime_error("Output must differ from input and model");
      m = fs::weakly_canonical(fs::absolute(model.value()));
      if (out.extension() != ".mp4" && out.extension() != ".mkv")
        throw std::runtime_error("Output must end in .mp4 or .mkv");
      if (fs::exists(out) && !overwrite.value())
        throw std::runtime_error(
            "Output exists; choose another path or enable replacement");
      Json job = {
          {"input", in.string()},
          {"output", out.string()},
          {"model", m.string()},
          {"device", device.value() == 0
                         ? (overrideDevice.empty()
                                ? (getenv("ARC_SBS_DEFAULT_DEVICE")
                                       ? getenv("ARC_SBS_DEFAULT_DEVICE")
                                       : "ARC")
                                : overrideDevice)
                         : "CPU"},
          {"renderer", device.value() == 0 ? "opencl" : "cpu"},
          {"encoder", encoder.value() == 0   ? "h264_vaapi"
                      : encoder.value() == 1 ? "libx264"
                      : encoder.value() == 2 ? "hevc_vaapi"
                                             : "av1_vaapi"},
          {"layout", layout.value() == 0 ? "half" : "full"},
          {"strength", strength.value()},
          {"convergence", convergence.value()},
          {"swap", bool(swap.value())},
          {"overwrite", bool(overwrite.value())},
          {"depth_processing", quality},
          {"pipeline", "overlap"},
          {"frames", frameLimit > 0 ? Json(frameLimit) : Json()},
          {"vaapi_device", vaapi}};
      pendingOutput = out.string();
      pendingLayout = layout.value() == 0 ? "half" : "full";
      pendingSwap = swap.value();
      preferences["model"] = m.string();
      preferences["input_dir"] = in.parent_path().string();
      store();
      char name[] = "/tmp/arc-sbs-fltk-XXXXXX";
      int tmp = mkstemp(name);
      if (tmp < 0)
        throw std::runtime_error("Cannot create private job file");
      jobfile = name;
      auto json = job.dump();
      if (write(tmp, json.data(), json.size()) != ssize_t(json.size())) {
        close(tmp);
        throw std::runtime_error("Cannot write job");
      }
      close(tmp);
      int pipes[2];
      if (pipe(pipes))
        throw std::runtime_error("pipe failed");
      posix_spawn_file_actions_t actions;
      posix_spawn_file_actions_init(&actions);
      posix_spawn_file_actions_addclose(&actions, pipes[0]);
      posix_spawn_file_actions_adddup2(&actions, pipes[1], STDOUT_FILENO);
      posix_spawn_file_actions_adddup2(&actions, pipes[1], STDERR_FILENO);
      posix_spawn_file_actions_addclose(&actions, pipes[1]);
      auto python = getenv("ARC_SBS_PYTHON");
      std::vector<std::string> arguments = {python ? python : "python3", "-u",
                                            "-m", "arc_sbs.gui_worker",
                                            jobfile.string()};
      std::vector<char *> argv;
      for (auto &value : arguments)
        argv.push_back(value.data());
      argv.push_back(nullptr);
      int error = posix_spawnp(&child, argv[0], &actions, nullptr, argv.data(),
                               environ);
      posix_spawn_file_actions_destroy(&actions);
      if (error) {
        close(pipes[0]);
        close(pipes[1]);
        child = -1;
        throw std::runtime_error("Cannot start conversion worker");
      }
      close(pipes[1]);
      fd = pipes[0];
      if (child < 0)
        throw std::runtime_error("fork failed");
      fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK);
      stopping = false;
      buffer.clear();
      messages.clear();
      progress.value(0);
      form.deactivate();
      start.deactivate();
      cancel.activate();
      showlog("Starting conversion...");
    } catch (const std::exception &e) {
      showlog(e.what());
      if (child <= 0)
        cleanup();
    }
  }

  std::filesystem::path settingsPath() {
    auto root = getenv("XDG_CONFIG_HOME");
    return std::filesystem::path(
               root ? root
                    : std::string(getenv("HOME") ? getenv("HOME") : "/tmp") +
                          "/.config") /
           "arc-sbs/fltk.json";
  }
  void store() {
    preferences["quality"] = quality;
    preferences["vaapi"] = vaapi;
    preferences["device_override"] = overrideDevice;
    preferences["frame_limit"] = frameLimit;
    preferences["strength"] = strength.value();
    preferences["convergence"] = convergence.value();
    preferences["layout"] = layout.value();
    preferences["encoder"] = encoder.value();
    try {
      auto target = settingsPath();
      std::filesystem::create_directories(target.parent_path());
      auto temporary = target;
      temporary += ".tmp";
      std::ofstream(temporary) << preferences.dump(2);
      std::filesystem::rename(temporary, target);
    } catch (...) {
    }
  }
  void options() {
    if (child > 0)
      return;
    dx::GpuWindow dialog(650, 330, "Conversion options");
    Fl_Choice processing(210, 25, 410, 30, "Depth processing");
    processing.add("Reference full resolution|Fast sampled "
                   "normalization|Experimental model resolution");
    processing.value(quality == "full" ? 0 : quality == "sampled" ? 1 : 2);
    Fl_Input gpu(210, 75, 410, 30, "GPU device override");
    gpu.value(overrideDevice.c_str());
    gpu.tooltip("Empty uses the package default; otherwise ARC, AMD, NVIDIA, "
                "GPU.N or a supported device name.");
    Fl_Input media(210, 125, 410, 30, "VA-API render device");
    media.value(vaapi.c_str());
    Fl_Int_Input frames(210, 175, 200, 30, "Frame limit (0 = all)");
    frames.value(std::to_string(frameLimit).c_str());
    Fl_Return_Button apply(440, 260, 180, 36, "Apply");
    Fl_Button close(220, 260, 180, 36, "Cancel");
    bool accepted = false;
    apply.callback(
        [](Fl_Widget *w, void *p) {
          *(bool *)p = true;
          w->window()->hide();
        },
        &accepted);
    close.callback([](Fl_Widget *w, void *) { w->window()->hide(); });
    dialog.end();
    dialog.set_modal();
    dx::style(&dialog);
    auto layoutDialog = [&] {
      int pad = 16, label = dx::size + 8, field = std::max(30, dx::size + 12),
          gap = 12;
      int width = std::max(650, dx::size * 30),
          height = pad * 2 + 4 * (label + field + gap) + field + gap;
      dialog.size(width, height);
      int y = pad + label;
      for (auto *widget :
           std::vector<Fl_Widget *>{&processing, &gpu, &media, &frames}) {
        widget->align(FL_ALIGN_TOP_LEFT);
        widget->resize(pad, y, width - pad * 2, field);
        y += label + field + gap;
      }
      close.resize(pad, height - pad - field, (width - pad * 3) / 2, field);
      apply.resize(pad * 2 + (width - pad * 3) / 2, height - pad - field,
                   (width - pad * 3) / 2, field);
      dialog.redraw();
    };
    layoutDialog();
    dialog.show();
    auto revision = dx::font_revision;
    while (dialog.shown()) {
      Fl::wait(.05);
      if (revision != dx::font_revision) {
        revision = dx::font_revision;
        layoutDialog();
      }
    }
    if (accepted) {
      try {
        auto limit = std::stoi(frames.value());
        if (limit < 0 || limit > 100000000)
          throw std::runtime_error("Frame limit must be 0–100000000");
        frameLimit = limit;
        quality = processing.value() == 0   ? "full"
                  : processing.value() == 1 ? "sampled"
                                            : "model";
        vaapi = media.value();
        overrideDevice = gpu.value();
        store();
      } catch (const std::exception &e) {
        showlog(e.what());
      }
    }
  }
  void command(const std::string &value) {
    if (value == "Open video")
      choose(source, Fl_Native_File_Chooser::BROWSE_FILE);
    else if (value == "Output path")
      choose(output, Fl_Native_File_Chooser::BROWSE_SAVE_FILE);
    else if (value == "Depth model")
      choose(model, Fl_Native_File_Chooser::BROWSE_FILE);
    else if (value == "Convert")
      begin();
    else if (value == "Cancel")
      stop();
    else if (value == "More options")
      options();
    else if (value == "Open output folder") {
      if (*output.value())
        dx::spawn({"gio", "open",
                   fs::absolute(output.value()).parent_path().string()});
    } else if (value == "Play output")
      playOutput();
    else if (value == "Quit")
      do_callback();
  }

public:
  bool capture(const char *path) {
    make_current();
    auto invalidate = [](auto &&self, Fl_Widget &widget) -> void {
      widget.damage(FL_DAMAGE_ALL);
      if (auto group = widget.as_group())
        for (int i = 0; i < group->children(); ++i) self(self, *group->child(i));
    };
    invalidate(invalidate, *this);
    draw();
    const int width = pixel_w(), height = pixel_h();
    std::vector<unsigned char> pixels(size_t(width) * height * 3);
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glReadPixels(0, 0, width, height, GL_RGB, GL_UNSIGNED_BYTE, pixels.data());
    if (glGetError() != GL_NO_ERROR) return false;
    std::ofstream stream(path, std::ios::binary);
    stream << "P6\n" << width << " " << height << "\n255\n";
    for (int row = height - 1; row >= 0; --row)
      stream.write(reinterpret_cast<const char *>(pixels.data() + size_t(row) * width * 3), width * 3);
    return bool(stream);
  }
  Converter() : dx::GpuWindow(800, 650, "Arc SBS") {
    xclass("arc-sbs");
    // Explicit parenting keeps the editable form separate from worker controls.
    form.end();
    footer.end();
    end();
    form.add(source);
    form.add(output);
    form.add(model);
    form.add(browseIn);
    form.add(browseOut);
    form.add(browseModel);
    form.add(device);
    form.add(layout);
    form.add(encoder);
    form.add(strength);
    form.add(convergence);
    form.add(swap);
    form.add(overwrite);
    add(menu);
    for (auto item : std::vector<std::pair<const char *, int>>{
             {"File/Open video", FL_CTRL + 'o'},
             {"File/Output path", FL_CTRL + 's'},
             {"File/Depth model", 0},
             {"File/Open output folder", 0},
             {"File/Play output", FL_CTRL + 'p'},
             {"File/Quit", FL_CTRL + 'q'},
             {"Conversion/Convert", FL_CTRL + FL_Enter},
             {"Conversion/Cancel", FL_Escape},
             {"Conversion/More options", FL_CTRL + ','}})
      menu.add(
          item.first, item.second,
          [](Fl_Widget *w, void *p) {
            auto bar = (Fl_Menu_ *)w;
            ((Converter *)p)->command(bar->mvalue()->label());
          },
          this);
    add(start);
    add(cancel);
    add(play);
    add(footer);
    footer.add(progress);
    footer.add(log);
    auto backend = getenv("ARC_SBS_DEFAULT_DEVICE");
    std::string gpuName =
        backend && std::string(backend) != "ARC" ? backend : "Intel Arc";
    device.add((gpuName + " (GPU)|CPU").c_str());
    device.value(0);
    layout.add("Half SBS|Full SBS");
    layout.value(0);
    encoder.add("H.264 (GPU)|H.264 (CPU)|HEVC (GPU)|AV1 (GPU)");
    encoder.value(getenv("ARC_SBS_DEFAULT_ENCODER") &&
                          std::string(getenv("ARC_SBS_DEFAULT_ENCODER")) ==
                              "libx264"
                      ? 1
                      : 0);
    strength.range(0, .1);
    strength.step(.001);
    strength.value(.025);
    convergence.range(0, 1);
    convergence.step(.01);
    convergence.value(.5);
    progress.minimum(0);
    progress.maximum(1);
    cancel.deactivate();
    auto m = getenv("ARC_SBS_MODEL");
    model.value(m ? m : "/usr/local/share/arc-sbs/models/depth-small.xml");
    browseIn.callback(
        [](Fl_Widget *, void *p) {
          auto s = (Converter *)p;
          s->choose(s->source, Fl_Native_File_Chooser::BROWSE_FILE);
        },
        this);
    browseOut.callback(
        [](Fl_Widget *, void *p) {
          auto s = (Converter *)p;
          s->choose(s->output, Fl_Native_File_Chooser::BROWSE_SAVE_FILE);
        },
        this);
    browseModel.callback(
        [](Fl_Widget *, void *p) {
          auto s = (Converter *)p;
          s->choose(s->model, Fl_Native_File_Chooser::BROWSE_FILE);
        },
        this);
    play.callback(
        [](Fl_Widget *, void *p) { static_cast<Converter *>(p)->playOutput(); },
        this);
    start.callback([](Fl_Widget *, void *p) { ((Converter *)p)->begin(); },
                   this);
    cancel.callback([](Fl_Widget *, void *p) { ((Converter *)p)->stop(); },
                    this);
    callback(
        [](Fl_Widget *, void *p) {
          auto s = (Converter *)p;
          if (s->child > 0) {
            s->stop();
            s->showlog("Close after the worker has stopped.");
          } else
            s->hide();
        },
        this);
    try {
      std::ifstream file(settingsPath());
      file >> preferences;
      quality = preferences.value("quality", std::string("full"));
      vaapi = preferences.value("vaapi", vaapi);
      overrideDevice = preferences.value("device_override", std::string());
      frameLimit = preferences.value("frame_limit", 0);
      model.value(
          preferences.value("model", std::string(model.value())).c_str());
      strength.value(std::clamp(preferences.value("strength", .025), 0., .1));
      convergence.value(
          std::clamp(preferences.value("convergence", .5), 0., 1.));
      layout.value(std::clamp(preferences.value("layout", 0), 0, 1));
      encoder.value(
          std::clamp(preferences.value("encoder", encoder.value()), 0, 3));
    } catch (...) {
      preferences = Json::object();
    }
    model.position(0);
    menu.layoutChanged = [this] { relayout(); };
    form.type(Fl_Scroll::VERTICAL);
    resizable(form);
    size_range(600, 420);
    relayout();
    desktop = std::make_unique<DesktopMenu>(*this, menu);
    Fl::add_timeout(.1, tick, this);
  }
  void relayout() {
    int pad = 12, field = std::max(30, dx::size + 12), label = dx::size + 8,
        gap = 10;
    fl_font(dx::font, dx::size);
    bool stackButtons = w() < int(fl_width("Play output") + 32) * 3 + pad * 4;
    int footerHeight = std::max(160, field * (stackButtons ? 3 : 2) + 80);
    int top = menu.h();
    menu.resize(0, 0, w(), menu.h());
    form.scroll_to(0, 0);
    form.resize(0, top, w(), std::max(80, h() - top - footerHeight));
    int scroll =
        form.scrollbar_size() ? form.scrollbar_size() : Fl::scrollbar_size();
    int available = w() - pad * 2 - scroll;
    int browse = std::max(85, int(dx::size * 5.5) + 24);
    int y = top + pad + label;
    for (auto pair : std::vector<std::pair<Fl_Input *, Fl_Button *>>{
             {&source, &browseIn},
             {&output, &browseOut},
             {&model, &browseModel}}) {
      pair.first->align(FL_ALIGN_TOP_LEFT);
      pair.first->resize(pad, y, available - browse - gap, field);
      pair.second->resize(pad + available - browse, y, browse, field);
      y += field + label + gap;
    }
    int column = (available - gap) / 2, right = pad + column + gap;
    for (auto pair : std::vector<std::pair<Fl_Widget *, Fl_Widget *>>{
             {&device, &layout},
             {&encoder, &strength},
             {&swap, &convergence}}) {
      pair.first->align(pair.first == &swap ? FL_ALIGN_LEFT | FL_ALIGN_INSIDE
                                            : FL_ALIGN_TOP_LEFT);
      pair.second->align(FL_ALIGN_TOP_LEFT);
      pair.first->resize(pad, y, column, field);
      pair.second->resize(right, y, column, field);
      y += field + label + gap;
    }
    overwrite.resize(pad, y, available, field);
    footer.resize(0, h() - footerHeight, w(), footerHeight);
    int bottom = h() - footerHeight + pad;
    int button = (w() - pad * (stackButtons ? 3 : 4)) / (stackButtons ? 2 : 3);
    start.resize(pad, bottom, button, field);
    cancel.resize(pad * 2 + button, bottom, button, field);
    if (stackButtons)
      play.resize(pad, bottom + field + gap, w() - pad * 2, field);
    else
      play.resize(pad * 3 + button * 2, bottom, button, field);
    int progressY = bottom + (field + gap) * (stackButtons ? 2 : 1);
    progress.resize(pad, progressY, w() - pad * 2, 20);
    log.resize(pad, progressY + 28, w() - pad * 2,
               std::max(30, h() - (progressY + 28) - pad));
    redraw();
  }
  void resize(int x, int y, int width, int height) override {
    dx::GpuWindow::resize(x, y, width, height);
    relayout();
  }
  void setSource(const std::string &path) {
    source.value(path.c_str());
    source.position(0);
    auto destination = fs::path(path).parent_path() /
                       (fs::path(path).stem().string() + "-sbs.mp4");
    output.value(destination.c_str());
    output.position(0);
  }
  void playOutput() {
    if (completedOutput.empty() || !fs::is_regular_file(completedOutput))
      return;
    std::vector<std::string> args = {
        "mpvr", "--xr-output=auto",
        "--sbs-layout=" +
            std::string(completedLayout == "half" ? "half-sbs" : "full-sbs")};
    if (completedSwap)
      args.push_back("--swap-eyes");
    args.push_back("--");
    args.push_back(completedOutput);
    auto pid = dx::spawn(args);
    if (pid < 0)
      showlog("Cannot start mpvr; check that it is installed.");
    else
      g_child_watch_add(
          pid, [](GPid child, gint, gpointer) { g_spawn_close_pid(child); },
          nullptr);
  }
  DesktopMenuBar &menuBar() { return menu; }
  std::string inputPath() const { return source.value(); }
  std::string outputPath() const { return output.value(); }
  int formTop() const { return form.y(); }
  bool busy() const { return child > 0; }
  bool canPlay() const { return play.active(); }
  void invoke(const std::string &name) { command(name); }
  bool layoutFits() const {
    for (auto *widget : std::vector<const Fl_Widget *>{
             &source, &output, &model, &device, &layout, &encoder, &strength,
             &convergence, &overwrite, &swap})
      if (widget->x() < 0 || widget->x() + widget->w() > w() ||
          widget->h() < dx::size)
        return false;
    for (auto *widget : std::vector<const Fl_Widget *>{&start, &cancel, &play})
      if (widget->y() + widget->h() > h() || widget->h() < dx::size)
        return false;
    return true;
  }
  int handle(int event) override {
    if (event == FL_SHORTCUT && Fl::event_key() == FL_Escape) {
      if (child > 0)
        stop();
      return 1;
    }
    return dx::GpuWindow::handle(event);
  }
  ~Converter() {
    store();
    Fl::remove_timeout(tick, this);
    if (child > 0) {
      kill(-child, SIGKILL);
      kill(child, SIGKILL);
      waitpid(child, nullptr, 0);
    }
    cleanup();
  }
  void stop() {
    if (child > 0 && !stopping) {
      stopping = true;
      stopped = std::chrono::steady_clock::now();
      kill(child, SIGTERM);
      showlog("Cancelling...");
    }
  }
};
#ifndef ARC_SBS_TESTING
int main(int argc, char **argv) try {
  bool x11 = false;
  std::string input, capture;
  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
    if (arg == "--x11")
      x11 = true;
    else if (arg == "--help") {
      std::cout << "Arc SBS: native FLTK Wayland video converter\nUsage: "
                   "arc-sbs-gui [VIDEO] [--x11]\n--x11 requires "
                   "-DARC_SBS_ENABLE_X11=ON at build time.\n";
      return 0;
    } else if (arg == "--smoke-test" && i + 1 < argc)
      capture = argv[++i];
    else if (arg.rfind("--", 0) == 0)
      throw std::runtime_error("Unknown frontend option: " + arg);
    else
      input = arg;
  }
#if !ARC_SBS_ENABLE_X11
  if (x11 ||
      (getenv("FLTK_BACKEND") && std::string(getenv("FLTK_BACKEND")) == "x11"))
    throw std::runtime_error(
        "X11 is a build-time opt-in (-DARC_SBS_ENABLE_X11=ON)");
#endif
  setenv("FLTK_BACKEND", x11 ? "x11" : "wayland", 1);
  dx::init();
  Converter app;
  if (!input.empty())
    app.setSource(input);
  dx::style(&app);
  app.show();
  if (!capture.empty()) {
    for (int i = 0; i < 24; ++i)
      Fl::wait(.025);
    if (!app.capture(capture.c_str()))
      throw std::runtime_error("Cannot capture frontend");
    std::cout << Json{{"toolkit", "FLTK"},
                      {"version", "1.4.5"},
                      {"font", dx::font_name},
                      {"size", dx::size},
                      {"universal_menu", app.menuBar().universal()},
                      {"menu_height", app.menuBar().h()},
                      {"form_top", app.formTop()},
                      {"input", app.inputPath()},
                      {"output", app.outputPath()}}
                     .dump()
              << '\n';
    app.hide();
    return 0;
  }
  return Fl::run();
} catch (const std::exception &e) {
  std::cerr << "Arc SBS: " << e.what() << '\n';
  return 1;
}
#endif
