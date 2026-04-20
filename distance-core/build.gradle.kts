plugins {
    java
    application
}

group = "com.hpcmap"
version = "0.1.0"

java {
    toolchain {
        languageVersion.set(JavaLanguageVersion.of(17))
    }
}

repositories {
    mavenCentral()
}

dependencies {
    implementation("com.graphhopper:graphhopper-web:11.0")
    testImplementation("org.junit.jupiter:junit-jupiter:5.10.2")
}

application {
    mainClass.set("com.hpcmap.distance.DistanceCoreMain")
}

tasks.test {
    useJUnitPlatform()
}
