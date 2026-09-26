#pragma once

#include <windows.h>

#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

// Line channel between the isolated worker and the external policy process.
// The handles are anonymous-pipe ends inherited from the launcher; the worker
// never opens files, pipes or processes by name.
struct PolicyReply {
    bool command{};
    // Simulation only: the policy has no action for this window, so the
    // original engine AI plays it (reported as such, never silently).
    bool automatic{};
    std::uint64_t sequence{};
    int actor_id{};
    int skill_type_id{};
    int target_id{};
    std::string reason;
};

class PolicyChannel {
public:
    PolicyChannel(HANDLE input, HANDLE output) : input_(input), output_(output) {
        if (!input_ || input_ == INVALID_HANDLE_VALUE || !output_ || output_ == INVALID_HANDLE_VALUE)
            throw std::runtime_error("Policy channel handles are unavailable");
    }

    void send(const std::string& line) {
        if (line.find('\n') != std::string::npos) throw std::runtime_error("Policy request contains a newline");
        write(line + "\n");
    }

    PolicyReply receive(std::uint64_t expected_sequence) {
        const auto line = read_line();
        std::vector<std::string> fields;
        std::size_t start = 0;
        while (true) {
            const auto end = line.find('\t', start);
            fields.push_back(line.substr(start, end == std::string::npos ? std::string::npos : end - start));
            if (end == std::string::npos) break;
            start = end + 1;
        }
        PolicyReply reply{};
        if (fields.size() == 3 && fields[0] == "stop") {
            reply.sequence = number(fields[1]);
            reply.reason = fields[2].substr(0, 128);
            for (char& c : reply.reason)
                if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == ':')) c = '_';
        } else if (fields.size() == 3 && fields[0] == "auto") {
            reply.automatic = true;
            reply.sequence = number(fields[1]);
            reply.reason = fields[2].substr(0, 128);
            for (char& c : reply.reason)
                if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == ':')) c = '_';
        } else if (fields.size() == 5 && fields[0] == "command") {
            reply.command = true;
            reply.sequence = number(fields[1]);
            reply.actor_id = static_cast<int>(number(fields[2]));
            reply.skill_type_id = static_cast<int>(number(fields[3]));
            reply.target_id = static_cast<int>(number(fields[4]));
            if (reply.skill_type_id <= 0) throw std::runtime_error("Policy reply has no skill type");
        } else {
            throw std::runtime_error("Policy reply is not a recognized line");
        }
        if (reply.sequence != expected_sequence) throw std::runtime_error("Policy reply sequence mismatch");
        return reply;
    }

private:
    HANDLE input_;
    HANDLE output_;
    std::string buffer_;

    static std::uint64_t number(const std::string& value) {
        if (value.empty() || value.size() > 12) throw std::runtime_error("Policy reply number is invalid");
        std::uint64_t result = 0;
        for (const char c : value) {
            if (c < '0' || c > '9') throw std::runtime_error("Policy reply number is invalid");
            result = result * 10 + static_cast<std::uint64_t>(c - '0');
        }
        if (result > 0x7fffffffULL) throw std::runtime_error("Policy reply number exceeds bound");
        return result;
    }

    void write(const std::string& data) {
        std::size_t offset = 0;
        while (offset < data.size()) {
            DWORD written = 0;
            const DWORD chunk = static_cast<DWORD>((std::min)(data.size() - offset, std::size_t(1) << 20));
            if (!WriteFile(output_, data.data() + offset, chunk, &written, nullptr) || !written)
                throw std::runtime_error("Policy channel write failed");
            offset += written;
        }
    }

    std::string read_line() {
        constexpr std::size_t max_line = 4096;
        while (true) {
            const auto end = buffer_.find('\n');
            if (end != std::string::npos) {
                std::string line = buffer_.substr(0, end);
                buffer_.erase(0, end + 1);
                if (!line.empty() && line.back() == '\r') line.pop_back();
                return line;
            }
            if (buffer_.size() > max_line) throw std::runtime_error("Policy reply exceeds line bound");
            char chunk[512];
            DWORD read = 0;
            if (!ReadFile(input_, chunk, sizeof(chunk), &read, nullptr) || !read)
                throw std::runtime_error("Policy channel closed");
            buffer_.append(chunk, read);
        }
    }
};
