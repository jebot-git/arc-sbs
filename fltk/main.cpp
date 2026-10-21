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
#include <FL/Fl_Value_Input.H>
#include <FL/fl_ask.H>
#include <chrono>
#include <fcntl.h>
#include <glib.h>
#include <signal.h>
namespace fs = std::filesystem;
class Converter : public dx::GpuWindow {
  Fl_Menu_Bar menu{0, 0, 800, 25};
  std::unique_ptr<DesktopMenu> desktop;
  Json preferences;
  std::string quality = "full",
              vaapi = "/dev/dri/by-path/pci-0000:03:00.0-render",
              overrideDevice;
  int frameLimit = 0;
  Fl_Group form{0, 25, 800, 395};
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
  Fl_Return_Button start{150, 380, 220, 32, "Convert"};
  Fl_Button cancel{390, 380, 150, 32, "Cancel"};
  Fl_Progress progress{20, 435, 760, 25};
  Fl_Multiline_Output log{20, 475, 760, 105};
  pid_t child = -1;
  int fd = -1;
  fs::path jobfile;
  std::string buffer, messages;
  bool stopping = false;
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
      field.value(c.filename());
      if (&field == &source && !*output.value())
        output.value((fs::path(source.value()).parent_path() /
                      (fs::path(source.value()).stem().string() + "-sbs.mp4"))
                         .c_str());
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
      showlog("Completed: " + j.value("result", Json::object()).dump());
      progress.value(1);
    } else if (kind == "cancelled")
      showlog("Cancelled");
    else if (kind == "progress") {
      auto phase = j.value("phase", std::string());
      int frame = j.value("frames", 0), total = j.value("total", 0);
      if (total > 0)
        progress.value(double(frame) / total);
      showlog(j.dump());
    }
  }
  void pump() {
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
    dialog.show();
    while (dialog.shown())
      Fl::wait();
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
    } else if (value == "Quit")
      do_callback();
  }

public:
  Converter() : dx::GpuWindow(800, 600, "Arc SBS — FLTK experiment") {
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
    add(footer);
    footer.add(progress);
    footer.add(log);
    device.add("Intel Arc|CPU");
    device.value(0);
    layout.add("Half SBS|Full SBS");
    layout.value(0);
    encoder.add("h264_vaapi|libx264|hevc_vaapi|av1_vaapi");
    encoder.value(0);
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
      encoder.value(std::clamp(preferences.value("encoder", 0), 0, 3));
    } catch (...) {
      preferences = Json::object();
    }
    desktop = std::make_unique<DesktopMenu>(*this, menu);
    Fl::add_timeout(.1, tick, this);
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
int main(int argc, char **argv) {
  dx::init();
  Converter app;
  dx::smoke(app, argc, argv);
  return Fl::run();
}
