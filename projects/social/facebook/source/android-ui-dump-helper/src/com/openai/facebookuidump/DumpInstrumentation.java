package com.openai.facebookuidump;

import android.app.Activity;
import android.app.Instrumentation;
import android.app.UiAutomation;
import android.graphics.Rect;
import android.os.Bundle;
import android.os.SystemClock;
import android.util.Xml;
import android.view.InputDevice;
import android.view.MotionEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import org.xmlpull.v1.XmlSerializer;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;

public final class DumpInstrumentation extends Instrumentation {
    private int nodeCount = 0;
    private Bundle arguments = new Bundle();

    @Override
    public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        if (arguments != null) this.arguments = arguments;
        writeStage("onCreate");
        start();
    }

    @Override
    public void onStart() {
        writeStage("onStart");
        Bundle result = new Bundle();
        File out = new File(getContext().getFilesDir(), "facebook-fast-ui.xml");
        try {
            writeStage("before-getUiAutomation");
            UiAutomation automation = getUiAutomation();
            writeStage("after-getUiAutomation");
            String mode = arguments.getString("mode", "dump");
            if ("server".equals(mode)) {
                int port = clamp(parseInt(arguments.getString("port"), 18766), 1024, 65535);
                runScrollServer(automation, port);
                result.putString("status", "ok");
                result.putString("mode", "server-stopped");
                finish(Activity.RESULT_OK, result);
                return;
            }
            if ("scroll".equals(mode)) {
                int count = clamp(parseInt(arguments.getString("count"), 1), 1, 8);
                int gapMs = clamp(parseInt(arguments.getString("gapMs"), 200), 0, 1000);
                ScrollResult scroll = performScrollBurst(automation, count, gapMs);
                result.putString("status", scroll.successCount > 0 ? "ok" : "error");
                result.putString("mode", "scroll");
                result.putInt("requested", count);
                result.putInt("successCount", scroll.successCount);
                result.putString("error", scroll.error);
                finish(scroll.successCount > 0 ? Activity.RESULT_OK : Activity.RESULT_CANCELED, result);
                return;
            }
            AccessibilityNodeInfo root = null;
            long deadline = SystemClock.uptimeMillis() + 4000L;
            while (root == null && SystemClock.uptimeMillis() < deadline) {
                writeStage("before-getRootInActiveWindow");
                root = automation.getRootInActiveWindow();
                writeStage(root == null ? "after-getRoot-null" : "after-getRoot-ok");
                if (root == null) SystemClock.sleep(100L);
            }
            if (root == null) throw new IllegalStateException("root-in-active-window-null");

            try (Writer writer = new OutputStreamWriter(new FileOutputStream(out), "UTF-8")) {
                XmlSerializer xml = Xml.newSerializer();
                xml.setOutput(writer);
                xml.startDocument("UTF-8", true);
                xml.startTag(null, "hierarchy");
                xml.attribute(null, "rotation", "0");
                dumpNode(xml, root, 0, 0);
                xml.endTag(null, "hierarchy");
                xml.endDocument();
                xml.flush();
            }
            result.putString("status", "ok");
            result.putString("path", out.getAbsolutePath());
            result.putInt("nodes", nodeCount);
            result.putString("rootPackage", str(root.getPackageName()));
            finish(Activity.RESULT_OK, result);
        } catch (Throwable t) {
            result.putString("status", "error");
            result.putString("error", t.getClass().getName() + ": " + t.getMessage());
            finish(Activity.RESULT_CANCELED, result);
        }
    }

    private void runScrollServer(UiAutomation automation, int port) throws Exception {
        writeStage("scroll-server-start");
        try (ServerSocket server = new ServerSocket(port, 1, InetAddress.getByName("127.0.0.1"))) {
            server.setSoTimeout(60000);
            while (true) {
                try (Socket socket = server.accept();
                     BufferedReader reader = new BufferedReader(new InputStreamReader(socket.getInputStream(), "UTF-8"));
                     Writer writer = new OutputStreamWriter(socket.getOutputStream(), "UTF-8")) {
                    String line = reader.readLine();
                    if (line == null) continue;
                    String[] parts = line.trim().split("\\s+");
                    String command = parts.length == 0 ? "" : parts[0].toUpperCase();
                    if ("PING".equals(command)) {
                        writer.write("OK PONG\n");
                        writer.flush();
                        continue;
                    }
                    if ("CHECK_REACTION".equals(command)) {
                        AccessibilityNodeInfo root = getActiveRoot(automation, 1500L);
                        AccessibilityNodeInfo target = null;
                        Rect rect = new Rect();
                        String error = "";
                        if (root == null) error = "root-null";
                        else if (!"com.facebook.katana".equals(str(root.getPackageName()))) error = "foreground-not-facebook";
                        else if (!hasReactionTitle(root, 0)) error = "reaction-title-not-confirmed";
                        else {
                            target = findReactionRecycler(root, 0, null);
                            if (target == null) error = "reaction-recyclerview-not-found";
                            else target.getBoundsInScreen(rect);
                        }
                        if (target != null) {
                            try { target.recycle(); } catch (Throwable ignored) { }
                        }
                        if (root != null) {
                            try { root.recycle(); } catch (Throwable ignored) { }
                        }
                        writer.write(error.isEmpty()
                                ? "OK REACTION " + rect.left + " " + rect.top + " " + rect.right + " " + rect.bottom + "\n"
                                : "ERR " + error + "\n");
                        writer.flush();
                        continue;
                    }
                    if ("STOP".equals(command)) {
                        writer.write("OK STOP\n");
                        writer.flush();
                        return;
                    }
                    if ("SCROLL".equals(command)) {
                        int count = clamp(parts.length > 1 ? parseInt(parts[1], 1) : 1, 1, 8);
                        int gapMs = clamp(parts.length > 2 ? parseInt(parts[2], 200) : 200, 0, 1000);
                        ScrollResult scroll = performScrollBurst(automation, count, gapMs);
                        if (scroll.successCount > 0) writer.write("OK SCROLL " + scroll.successCount + "\n");
                        else writer.write("ERR " + scroll.error + "\n");
                        writer.flush();
                        continue;
                    }
                    if ("SWIPE".equals(command)) {
                        int count = clamp(parts.length > 1 ? parseInt(parts[1], 1) : 1, 1, 8);
                        int gapMs = clamp(parts.length > 2 ? parseInt(parts[2], 200) : 200, 0, 1000);
                        int durationMs = clamp(parts.length > 3 ? parseInt(parts[3], 300) : 300, 80, 1200);
                        int startY = clamp(parts.length > 4 ? parseInt(parts[4], 1700) : 1700, 1, 4000);
                        int endY = clamp(parts.length > 5 ? parseInt(parts[5], 250) : 250, 1, 4000);
                        ScrollResult swipe = performGestureBurst(automation, count, gapMs, durationMs, startY, endY);
                        if (swipe.successCount > 0) writer.write("OK SWIPE " + swipe.successCount + "\n");
                        else writer.write("ERR " + swipe.error + "\n");
                        writer.flush();
                        continue;
                    }
                    writer.write("ERR unsupported-command\n");
                    writer.flush();
                }
            }
        }
    }

    private AccessibilityNodeInfo getActiveRoot(UiAutomation automation, long timeoutMs) {
        AccessibilityNodeInfo root = null;
        long deadline = SystemClock.uptimeMillis() + Math.max(0L, timeoutMs);
        do {
            root = automation.getRootInActiveWindow();
            if (root != null) return root;
            SystemClock.sleep(100L);
        } while (SystemClock.uptimeMillis() < deadline);
        return null;
    }

    private ScrollResult performScrollBurst(UiAutomation automation, int count, int gapMs) {
        int successes = 0;
        for (int i = 0; i < count; i++) {
            AccessibilityNodeInfo root = automation.getRootInActiveWindow();
            if (root == null) return new ScrollResult(successes, "root-null");
            if (!"com.facebook.katana".equals(str(root.getPackageName()))) return new ScrollResult(successes, "foreground-not-facebook");
            if (!hasReactionTitle(root, 0)) return new ScrollResult(successes, "reaction-title-not-confirmed");
            AccessibilityNodeInfo target = findReactionRecycler(root, 0, null);
            if (target == null) return new ScrollResult(successes, "reaction-recyclerview-not-found");
            boolean ok = target.performAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD);
            try { target.recycle(); } catch (Throwable ignored) { }
            if (!ok) return new ScrollResult(successes, "scroll-action-rejected");
            successes++;
            if (i + 1 < count && gapMs > 0) SystemClock.sleep(gapMs);
        }
        return new ScrollResult(successes, "");
    }

    private ScrollResult performGestureBurst(UiAutomation automation, int count, int gapMs, int durationMs, int startY, int endY) {
        int successes = 0;
        for (int i = 0; i < count; i++) {
            AccessibilityNodeInfo root = automation.getRootInActiveWindow();
            if (root == null) return new ScrollResult(successes, "root-null");
            if (!"com.facebook.katana".equals(str(root.getPackageName()))) return new ScrollResult(successes, "foreground-not-facebook");
            if (!hasReactionTitle(root, 0)) return new ScrollResult(successes, "reaction-title-not-confirmed");
            AccessibilityNodeInfo target = findReactionRecycler(root, 0, null);
            if (target == null) return new ScrollResult(successes, "reaction-recyclerview-not-found");
            Rect rect = new Rect();
            target.getBoundsInScreen(rect);
            try { target.recycle(); } catch (Throwable ignored) { }
            float x = Math.max(rect.left + 10, Math.min(rect.right - 10, rect.centerX()));
            float y1 = Math.max(rect.top + 10, Math.min(rect.bottom - 10, startY));
            float y2 = Math.max(rect.top + 10, Math.min(rect.bottom - 10, endY));
            if (Math.abs(y1 - y2) < 120f) return new ScrollResult(successes, "gesture-range-too-small");
            if (!injectSwipe(automation, x, y1, y2, durationMs)) return new ScrollResult(successes, "gesture-injection-failed");
            successes++;
            if (i + 1 < count && gapMs > 0) SystemClock.sleep(gapMs);
        }
        return new ScrollResult(successes, "");
    }

    private boolean injectSwipe(UiAutomation automation, float x, float startY, float endY, int durationMs) {
        long downTime = SystemClock.uptimeMillis();
        MotionEvent down = MotionEvent.obtain(downTime, downTime, MotionEvent.ACTION_DOWN, x, startY, 0);
        down.setSource(InputDevice.SOURCE_TOUCHSCREEN);
        try {
            if (!automation.injectInputEvent(down, true)) return false;
        } finally {
            down.recycle();
        }
        int steps = Math.max(6, Math.min(20, durationMs / 20));
        for (int step = 1; step < steps; step++) {
            long targetTime = downTime + ((long)durationMs * step / steps);
            long sleepMs = targetTime - SystemClock.uptimeMillis();
            if (sleepMs > 0) SystemClock.sleep(sleepMs);
            float fraction = (float)step / (float)steps;
            float y = startY + ((endY - startY) * fraction);
            MotionEvent move = MotionEvent.obtain(downTime, SystemClock.uptimeMillis(), MotionEvent.ACTION_MOVE, x, y, 0);
            move.setSource(InputDevice.SOURCE_TOUCHSCREEN);
            try {
                if (!automation.injectInputEvent(move, true)) return false;
            } finally {
                move.recycle();
            }
        }
        long finalSleep = (downTime + durationMs) - SystemClock.uptimeMillis();
        if (finalSleep > 0) SystemClock.sleep(finalSleep);
        MotionEvent up = MotionEvent.obtain(downTime, SystemClock.uptimeMillis(), MotionEvent.ACTION_UP, x, endY, 0);
        up.setSource(InputDevice.SOURCE_TOUCHSCREEN);
        try {
            return automation.injectInputEvent(up, true);
        } finally {
            up.recycle();
        }
    }

    private AccessibilityNodeInfo findReactionRecycler(AccessibilityNodeInfo node, int depth, AccessibilityNodeInfo best) {
        if (node == null || depth > 80) return best;
        Rect rect = new Rect();
        node.getBoundsInScreen(rect);
        String cls = str(node.getClassName());
        String pkg = str(node.getPackageName());
        if ("com.facebook.katana".equals(pkg) && node.isScrollable() && cls.contains("RecyclerView") &&
                rect.top >= 180 && rect.bottom >= 800 && rect.height() >= 500) {
            if (best == null) best = AccessibilityNodeInfo.obtain(node);
            else {
                Rect bestRect = new Rect();
                best.getBoundsInScreen(bestRect);
                if (rect.height() > bestRect.height() ||
                        (rect.height() == bestRect.height() && node.getChildCount() > best.getChildCount())) {
                    try { best.recycle(); } catch (Throwable ignored) { }
                    best = AccessibilityNodeInfo.obtain(node);
                }
            }
        }
        int children = node.getChildCount();
        for (int i = 0; i < children; i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                best = findReactionRecycler(child, depth + 1, best);
                try { child.recycle(); } catch (Throwable ignored) { }
            }
        }
        return best;
    }

    private boolean hasReactionTitle(AccessibilityNodeInfo node, int depth) {
        if (node == null || depth > 80) return false;
        String text = str(node.getText()).trim();
        String desc = str(node.getContentDescription()).trim();
        if (isReactionTitle(text) || isReactionTitle(desc)) return true;
        int children = node.getChildCount();
        for (int i = 0; i < children; i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) {
                boolean found = hasReactionTitle(child, depth + 1);
                try { child.recycle(); } catch (Throwable ignored) { }
                if (found) return true;
            }
        }
        return false;
    }

    private static boolean isReactionTitle(String value) {
        return "Reactions".equalsIgnoreCase(value)
                || "Reaction".equalsIgnoreCase(value)
                || "心情".equals(value)
                || value.startsWith("心情选项卡栏")
                || value.toLowerCase().startsWith("reaction tab bar");
    }

    private static int parseInt(String value, int fallback) {
        try { return Integer.parseInt(value); } catch (Throwable ignored) { return fallback; }
    }

    private static int clamp(int value, int min, int max) {
        return Math.max(min, Math.min(max, value));
    }

    private static final class ScrollResult {
        final int successCount;
        final String error;
        ScrollResult(int successCount, String error) {
            this.successCount = successCount;
            this.error = error == null ? "" : error;
        }
    }

    private void dumpNode(XmlSerializer xml, AccessibilityNodeInfo node, int index, int depth) throws Exception {
        if (node == null || depth > 200 || nodeCount >= 20000) return;
        nodeCount++;
        Rect r = new Rect();
        node.getBoundsInScreen(r);
        xml.startTag(null, "node");
        attr(xml, "index", Integer.toString(index));
        attr(xml, "text", str(node.getText()));
        attr(xml, "resource-id", str(node.getViewIdResourceName()));
        attr(xml, "class", str(node.getClassName()));
        attr(xml, "package", str(node.getPackageName()));
        attr(xml, "content-desc", str(node.getContentDescription()));
        attr(xml, "checkable", bool(node.isCheckable()));
        attr(xml, "checked", bool(node.isChecked()));
        attr(xml, "clickable", bool(node.isClickable()));
        attr(xml, "enabled", bool(node.isEnabled()));
        attr(xml, "focusable", bool(node.isFocusable()));
        attr(xml, "focused", bool(node.isFocused()));
        attr(xml, "scrollable", bool(node.isScrollable()));
        attr(xml, "long-clickable", bool(node.isLongClickable()));
        attr(xml, "password", bool(node.isPassword()));
        attr(xml, "selected", bool(node.isSelected()));
        attr(xml, "bounds", "[" + r.left + "," + r.top + "][" + r.right + "," + r.bottom + "]");
        int children = node.getChildCount();
        for (int i = 0; i < children; i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) dumpNode(xml, child, i, depth + 1);
        }
        xml.endTag(null, "node");
    }

    private static void attr(XmlSerializer xml, String name, String value) throws Exception {
        xml.attribute(null, name, value == null ? "" : value);
    }
    private static String str(CharSequence value) { return value == null ? "" : value.toString(); }
    private static String bool(boolean value) { return value ? "true" : "false"; }
    private void writeStage(String stage) {
        try {
            File f = new File(getContext().getFilesDir(), "stage.txt");
            try (Writer w = new OutputStreamWriter(new FileOutputStream(f, false), "UTF-8")) {
                w.write(stage + "\n");
                w.flush();
            }
        } catch (Throwable ignored) { }
    }

}
