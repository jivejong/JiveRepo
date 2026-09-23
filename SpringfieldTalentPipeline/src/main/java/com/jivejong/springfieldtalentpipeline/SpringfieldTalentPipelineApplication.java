package com.jivejong.springfieldtalentpipeline;

import com.jivejong.springfieldtalentpipeline.config.GeminiProperties;
import com.jivejong.springfieldtalentpipeline.config.SimpsonsApiProperties;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;

@SpringBootApplication
@EnableConfigurationProperties({GeminiProperties.class, SimpsonsApiProperties.class})
public class SpringfieldTalentPipelineApplication {

    public static void main(String[] args) {
        SpringApplication.run(SpringfieldTalentPipelineApplication.class, args);
    }
}
